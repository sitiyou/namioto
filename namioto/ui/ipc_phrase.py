# SPDX-License-Identifier: AGPL-3.0-only
"""Phrase evidence and validated single-undo edits on the editor's audio clock."""

from __future__ import annotations

import hashlib
import json

from namioto import i18n
from namioto.document import MIN_DURATION
from namioto.ipc import IpcError
from namioto.karaoke.canonical import rebuild
from namioto.karaoke.operations import NoteRun, SoundRef
from namioto.lyricmap import faithful
from namioto.lyricmap.channels import split
from namioto.lyricmap.notes import TimedNote, resolve
from namioto.ui.ipc_commands import _flag, _number, _text, _whole


def ownership(part, notes):
    resolved = resolve(notes, include_branches=False)
    _, ids = faithful.lay_out(part.text, resolved.stream)
    return (
        {ref: row[i] for refs, row in zip(part.refs, ids, strict=True) for i, ref in enumerate(refs)},
        {note.id for note in resolved.filtered},
    )


def wire(note):
    return {"id": note.id, "pitch": note.pitch, "start": note.start, "duration": note.end - note.start}


class Phrase:
    def __init__(self, window, line, channel):
        self.window = window
        self.text = window.lyrics_text
        self.line = line
        self.channel = channel
        self.part = split(self.text).get(channel)
        if self.part is None:
            raise IpcError("bad_params", "No lyrics on this channel")
        self.all_refs = [ref for row in self.part.refs for ref in row]
        self.refs = [ref for ref in self.all_refs if ref.line == line]
        if not self.refs:
            raise IpcError("bad_params", "No sounds for this channel on this display line")
        self.notes = [TimedNote(*row) for row in window._target_notes(channel)]
        self.ids, self.filtered = ownership(self.part, self.notes)
        self.owned = {i for ref in self.refs for i in self.ids[ref]}
        self.layout = faithful.structure(self.text)
        self.runs = [run for run in self.layout.runs if run.sounds[0] in self.refs]
        first = self.all_refs.index(self.refs[0])
        neighborhood = self.all_refs[max(first - 1, 0) : first + len(self.refs) + 1]
        nearby_ids = {i for ref in neighborhood for i in self.ids[ref]}
        version = [
            window.audio_path,
            str(window.project_path),
            self.layout.lines[line].text,
            self.mapping(),
            line,
            channel,
            [(r.line, r.index, self.layout.lines[r.line].sounds[r.index].reading, self.ids[r]) for r in neighborhood],
            [n for n in self.notes if n.id in nearby_ids],
            window.view.bpm,
            window.view.offset,
        ]
        self.revision = hashlib.sha256(json.dumps(version).encode()).hexdigest()

    def mapping(self):
        return [{"sounds": [ref.index for ref in run.sounds], "slots": run.slots} for run in self.runs]

    def inspect(self, bridge, params, context):
        first = self.all_refs.index(self.refs[0])
        refs = self.all_refs[max(0, first - context) : first + len(self.refs) + context]
        local = self.part.local
        stamp = bridge.audio_stamp()
        jobs = [
            job
            for job in bridge._analysis_jobs.values()
            if job.get("status") == "complete"
            and job.get("audio") == self.window.audio_path
            and job["audio_stamp"] == stamp
        ]
        signature = bridge.alignment_key(self.text, self.channel)
        models = {
            job["model"]: job
            for job in jobs
            if job.get("channel") == self.channel and job.get("lyrics_key") == signature
        }
        times = []
        sounds = []
        for ref in refs:
            sound = self.layout.lines[ref.line].sounds[ref.index]
            pos = local[ref]
            align = {}
            for model, job in models.items():
                align[model] = {key: job[key][pos.line][pos.index] for key in ("times", "scores", "onset_bounds")}
                times.extend(t for t in align[model]["times"] if t is not None)
            sounds.append(
                {
                    "line": ref.line,
                    "index": ref.index,
                    "base": sound.base,
                    "reading": sound.reading,
                    "note_ids": self.ids[ref],
                    "alignment": align,
                }
            )
        ids = {i for ref in refs for i in self.ids[ref]}
        notes = [n for n in self.notes if n.id in ids]
        times.extend(t for n in notes for t in (n.start, n.end))
        start = _number(params, "start", low=0, required=False, default=min(times, default=0))
        end = _number(params, "end", low=start, required=False, default=max([start, *times]))
        cells = _whole(params, "cells", required=False, default=2)
        if cells not in (1, 2, 4, 8, 16, 32):
            raise IpcError("bad_params", "Unsupported subdivisions per quarter note")
        estimate_phase = _flag(params, "estimate_phase", default=True)
        include_summary = _flag(params, "signal_summary", default=True)
        bpm = self.window.view.bpm
        game = next((job for job in reversed(jobs) if "notes" in job), None)
        result = {
            "line": self.line,
            "channel": self.channel,
            "revision": self.revision,
            "audio": self.window.audio_path,
            "time_unit": "seconds",
            "start": start,
            "end": end,
            "text": self.layout.lines[self.line].text,
            "sounds": sounds,
            "mapping": self.mapping(),
            "notes": [wire(n) for n in notes if n.id in self.owned],
            "context_notes": [wire(n) for n in notes if n.id not in self.owned],
            "alignment_jobs": {m: j["job"] for m, j in models.items()},
            "missing": [m for m in ("mms", "yohane") if m not in models],
            "grid": {
                "bpm": bpm,
                "cells": cells,
                "cell_seconds": 60 / bpm / cells,
                "editor_offset_ms": round(self.window.view.offset * 1000),
            },
        }
        quantization = []
        if game:
            grid = bridge.transcription_grid({"job": game["job"], "cells": cells, "estimate_phase": estimate_phase})

            def section(rows):
                return [n for n in rows if n["onset"] < end and n["offset"] > start]

            quantization = [
                row
                for row in grid["quantization"]
                if (row["onset"]["raw"] < end and row["offset"]["raw"] > start)
                or (
                    row["quantized_index"] is not None
                    and row["onset"]["snapped"] < end
                    and row["offset"]["snapped"] > start
                )
            ]
            result["game"] = {
                "job": game["job"],
                "offset_ms": grid["offset_ms"],
                "raw": section(game["notes"]),
                "quantized": section(grid["notes"]),
                "quantization": quantization,
            }
            if estimate_phase:
                result["grid"]["estimated_offset_ms"] = grid["offset_ms"]
        else:
            result["missing"].append("game")
        if include_summary:
            self._add_signal_summary(result, quantization, notes, start, end)
        if _flag(params, "spectrum"):
            image = self.window.view.spectrum
            if image is None:
                result["missing"].append("spectrum")
            else:
                spectrum = image.spectrum
                from namioto.analysis.spectrum import MIDI_OFFSET

                a = min(spectrum.frames, spectrum.frame_at(start)) if start < spectrum.duration else spectrum.frames
                b = min(spectrum.frames, spectrum.frame_at(end) + 1) if end < spectrum.duration else spectrum.frames
                stop = min(b, a + 256)
                result["spectrum"] = {
                    "start": spectrum.time_at(a),
                    "step": spectrum.frame_ms / 1000,
                    "first_pitch": MIDI_OFFSET,
                    "frames": spectrum.table[a:stop].tolist(),
                    "next_start": spectrum.time_at(stop) if stop < b else None,
                }
        if _flag(params, "rms"):
            result["rms"] = rms_window(self.window.audio_path, start, end)
            if result["rms"] is None:
                result["missing"].append("rms")
        return result

    def _add_signal_summary(self, result, quantization, notes, start, end):
        from namioto.analysis.phrase_signal import summarize

        if quantization:
            identity = [{"raw_index": row["raw_index"], "pitch": row["pitch"]} for row in quantization]
            ranges = [
                tuple((row["onset"][kind], row["offset"][kind], row["pitch"]) for kind in ("raw", "snapped"))
                for row in quantization
            ]
        else:
            notes = [note for note in notes if note.start < end and note.end > start]
            identity = [{"note_id": note.id, "pitch": note.pitch} for note in notes]
            ranges = [((note.start, note.end, note.pitch),) * 2 for note in notes]
        if not ranges:
            return
        path = self.window.audio_path
        summary = summarize(path, ranges, self.window.project_settings.analysis.a4) if path else None
        if summary is None:
            result["missing"].append("signal_summary")
            return
        for row, key in zip(summary["notes"], identity, strict=True):
            row.update(key)
        result["signal_summary"] = summary

    def apply(self, bridge, params):
        if _text(params, "revision") != self.revision:
            raise IpcError("conflict", "Phrase source changed; read it again")
        view = self.window.view
        if view.gesture_active or view._locked(self.channel):
            raise IpcError("busy", "Finish the gesture or unlock the channel first")
        preview = _flag(params, "preview", True)
        groups, entries = params.get("mapping"), params.get("notes")
        if not isinstance(groups, list) or not isinstance(entries, list):
            raise IpcError("bad_params", "Supply mapping and notes arrays")
        runs = []
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("sounds"), list):
                raise IpcError("bad_params", "Each group needs sound indices and slots")
            refs = tuple(SoundRef(self.line, _whole({"i": i}, "i", low=0)) for i in group["sounds"])
            runs.append(NoteRun(refs, _whole(group, "slots", low=0)))
        if [r for run in runs for r in run.sounds] != self.refs or sum(run.slots for run in runs) != len(entries):
            raise IpcError("bad_params", "Cover the phrase sounds in order and supply one note per slot")
        plan = [r for r in self.layout.runs if r not in self.runs] + runs
        text = self.text if runs == self.runs else rebuild(self.text, plan, line_indices=[self.line])
        part = split(text)[self.channel]
        arriving = []
        next_id = view.document.next_id
        for entry in entries:
            pitch, start, duration, channel = bridge._note_spec(entry)
            if channel not in (None, self.channel) or view.to_beats(duration) < MIN_DURATION - 1e-9:
                raise IpcError("bad_params", "Invalid note channel or duration below editor minimum")
            if "id" in entry:
                key = _whole(entry, "id", low=1)
                if key not in self.owned:
                    raise IpcError("bad_params", "Note ID belongs outside this phrase")
            else:
                key = next_id
                next_id += 1
            arriving.append(TimedNote(start, start + duration, pitch, key))
        keys = [n.id for n in arriving]
        if len(set(keys)) != len(keys) or arriving != sorted(arriving):
            raise IpcError("bad_params", "Notes must be ordered with unique IDs")
        kept = [n for n in self.notes if n.id not in self.owned]
        ids, filtered = ownership(part, kept + arriving)
        outside = [ref for ref in self.all_refs if ref.line != self.line]
        if (
            filtered != self.filtered
            or {i for r in self.refs for i in ids[r]} != set(keys)
            or any(ids[r] != self.ids[r] for r in outside)
        ):
            raise IpcError("bad_params", "Edit overlaps another note or changes ownership outside the phrase")
        following = next((r for r in outside if r.line > self.line), None)
        old = [wire(n) for n in self.notes if n.id in self.owned]
        new = [wire(n) for n in arriving]
        if not preview and (old != new or text != self.text):
            carry = [(n.pitch, n.start, n.duration, n.channel, n.id) for n in view.notes() if n.id not in self.owned]
            with bridge.group_changes(), view.lyric_edit(i18n.tr("Edit lyrics")):
                if not self.window._on_lyrics_saved(text):
                    raise IpcError("failed", "Could not apply the mapping")
                view.set_notes(
                    carry
                    + [
                        (n.pitch, view.to_beats(n.start), view.to_beats(n.end - n.start), self.channel, n.id)
                        for n in arriving
                    ]
                )
        return {
            "preview": preview,
            "before": {"notes": old, "mapping": self.mapping()},
            "after": {"notes": new, "mapping": groups},
            "next_sound": None
            if following is None
            else {
                "line": following.line,
                "index": following.index,
                "before": self.ids[following],
                "after": ids[following],
            },
        }


def rms_window(path, start, end):
    """20 ms RMS per source channel, up to 256 windows per page."""
    import numpy as np
    import soundfile as sf

    if not path:
        return None
    try:
        with sf.SoundFile(path) as audio:
            rate = audio.samplerate
            step = max(1, round(rate * 0.020))
            a = min(len(audio), round(start * rate))
            b = min(len(audio), round(end * rate))
            audio.seek(a)
            samples = audio.read(min(256 * step, b - a), always_2d=True)
    except OSError, sf.LibsndfileError:
        return None
    values = [np.sqrt(np.mean(samples[i : i + step] ** 2, axis=0)).tolist() for i in range(0, len(samples), step)]
    return {
        "start": a / rate,
        "step": step / rate,
        "linear_rms": values,
        "next_start": (a + len(samples)) / rate if a + len(samples) < b else None,
    }
