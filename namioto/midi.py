# SPDX-License-Identifier: AGPL-3.0-only
"""MIDI files: reading them into notes and tracks, and writing those back out.

Qt-free on purpose, like project.py and settings.py. mido is a file codec here and nothing else: no
port is ever opened, so a machine with no sound card and a test read and write the same.

Notes are stored in seconds, as everywhere else in the project, so the tempo map of a file is walked
on the way in and written as the one tempo the grid has on the way out.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

import mido

from namioto import project
from namioto import settings as store
from namioto.tracks import TRACK_LIMIT, Track

SUFFIXES = (".mid", ".midi")
PPQ = 960  # the beat grid: a tick is well under a millisecond
EXACT_PPQ = 10000  # exact time: at 60 BPM a tick is 0.1 ms, the precision a project keeps
EXACT_BPM = 60.0
VELOCITY = 100  # a note carries no velocity of its own, so every one is written alike
DEFAULT_VOLUME = 100  # MIDI's own channel volume when the file says nothing
LEAD_IN_BEATS = 4  # WaveTone's export starts one bar late; see `read` and `write`


def looks_like_midi(path: str | Path) -> bool:
    return Path(path).suffix.lower() in SUFFIXES


@dataclass(frozen=True)
class Imported:
    """What a MIDI file holds, in the project's terms."""

    tracks: tuple[Track, ...]
    notes: tuple[project.Note, ...]
    bpm: float
    tempo_changes: int = 0  # tempo events past the first, which the single grid cannot follow
    dropped: int = 0  # note events that never made a note: no pair, or no length
    left_out: tuple[int, ...] = ()  # channels past the track limit


def read(path: str | Path, *, wavetone: bool = False, limit: int = TRACK_LIMIT) -> Imported:
    """Read a MIDI file into tracks and notes.

    Raises OSError, EOFError or ValueError for a file that is not MIDI, or is cut short.
    """
    mid = _load(path)
    ppq = max(1, int(mid.ticks_per_beat))
    tempos = _tempo_map(mid)
    lead_in = LEAD_IN_BEATS * ppq if wavetone else 0

    events: list[tuple[int, int, int, int]] = []  # (tick, channel, pitch, velocity), 0 meaning off
    programs: dict[int, dict[int, int]] = {}
    volumes: dict[int, dict[int, int]] = {}
    names: dict[int, set[str]] = {}
    for track in mid.tracks:
        tick = 0
        channels: set[int] = set()
        name = next((message.name for message in track if message.type == "track_name"), "")
        for message in track:
            tick += message.time
            if message.type in ("note_on", "note_off"):
                channels.add(message.channel)
                velocity = message.velocity if message.type == "note_on" else 0
                events.append((tick, message.channel, message.note, velocity))
            elif message.type == "program_change":
                programs.setdefault(message.channel, {})[tick] = message.program
            elif message.type == "control_change" and message.control == 7:
                volumes.setdefault(message.channel, {})[tick] = message.value
        for channel in channels:
            if name:
                names.setdefault(channel, set()).add(name)

    # an off sorts before an on of the same tick, so a note ending where the next one starts pairs
    events.sort(key=lambda event: (event[0], event[3]))
    if lead_in and min((tick for tick, _c, _p, velocity in events if velocity > 0), default=0) < lead_in:
        lead_in = 0  # a file whose notes start before that bar is not one WaveTone wrote
    pending: dict[tuple[int, int], list[int]] = {}
    played: list[tuple[int, int, int, int]] = []  # (channel, start, end, pitch) in file ticks
    dropped = 0
    for tick, channel, pitch, velocity in events:
        key = (channel, pitch)
        if velocity > 0:
            pending.setdefault(key, []).append(tick)
            continue
        queue = pending.get(key)
        if not queue:
            dropped += 1
            continue
        start = max(0, queue.pop(0) - lead_in)
        end = max(0, tick - lead_in)
        if end <= start:
            dropped += 1
            continue
        played.append((channel, start, end, pitch))
    dropped += sum(len(queue) for queue in pending.values())
    played.sort(key=lambda note: (note[1], note[3], note[0]))  # by start, then pitch, then channel

    present = sorted({channel for channel, _start, _end, _pitch in played})
    kept, left_out = present[:limit], tuple(present[limit:])
    index_of = {channel: index for index, channel in enumerate(kept)}

    tracks: list[Track] = []
    for index, channel in enumerate(kept):
        first = min(start for source, start, _end, _pitch in played if source == channel)
        name = next(iter(names.get(channel, ())), "").strip()
        tracks.append(
            Track(
                name=name[: store.TEXT_LIMIT] or f"Track {index + 1}",
                channel=channel,
                program=_before(programs.get(channel, {}), first, 0),
                volume=_before(volumes.get(channel, {}), first, DEFAULT_VOLUME),
            )
        )
    notes = tuple(
        project.Note(
            _seconds(tempos, start, ppq),
            _seconds(tempos, end, ppq) - _seconds(tempos, start, ppq),
            pitch,
            index_of[channel],
        )
        for channel, start, end, pitch in played
        if channel in index_of
    )
    return Imported(
        tracks=tuple(tracks),
        notes=notes,
        bpm=_bpm(tempos),
        tempo_changes=len(tempos) - 1,
        dropped=dropped,
        left_out=left_out,
    )


def write(
    path: str | Path,
    tracks,
    notes,
    bpm: float,
    *,
    wavetone: bool = False,
    exact: bool = False,
    quantize: float = 0.0,
    included=None,
) -> Path:
    """Write tracks and notes, both in seconds, out as a MIDI file.

    `exact` keeps the times the project holds down to the tick under a tempo of 60, which is what a
    transcription from audio wants; without it the notes go on the project's own beats, where
    `quantize` (a grid in beats) rounds them onto it.
    """
    ppq = EXACT_PPQ if exact else PPQ
    grid = EXACT_BPM if exact else max(1.0, float(bpm))
    seconds_per_beat = 60.0 / grid
    quantize = 0.0 if exact else quantize  # exact time is the point of that mode, not a grid
    lead_in = LEAD_IN_BEATS * ppq if wavetone else 0

    mid = mido.MidiFile(type=1, ticks_per_beat=ppq, charset="utf-8")
    conductor = mido.MidiTrack()
    conductor.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(grid), time=0))
    conductor.append(mido.MetaMessage("time_signature", numerator=4, denominator=4, time=0))
    mid.tracks.append(conductor)

    for index in range(len(tracks)) if included is None else included:
        track = tracks[index]
        events: list[tuple[int, int, int]] = []
        for note in notes:
            if note.track != index:
                continue
            start = _tick(note.start, seconds_per_beat, ppq, quantize)
            end = _tick(note.start + note.duration, seconds_per_beat, ppq, quantize)
            events.append((start, 1, note.pitch))
            events.append((max(end, start + 1), 0, note.pitch))
        events.sort()  # a note ending where the next one starts lets go of the pitch first
        written = mido.MidiTrack()
        name = track.name.strip()
        if name:
            written.append(mido.MetaMessage("track_name", name=name, time=0))
        written.append(mido.Message("program_change", channel=track.channel, program=track.program, time=0))
        written.append(mido.Message("control_change", channel=track.channel, control=7, value=track.volume, time=0))
        previous = 0
        for tick, kind, pitch in events:
            tick += lead_in
            written.append(
                mido.Message(
                    "note_on",
                    channel=track.channel,
                    note=pitch,
                    velocity=VELOCITY if kind else 0,
                    time=tick - previous,
                )
            )
            previous = tick
        mid.tracks.append(written)
    return _save(path, mid)


def _tick(seconds: float, seconds_per_beat: float, ppq: int, quantize: float) -> int:
    beats = seconds / seconds_per_beat
    if quantize > 0.0:
        beats = round(beats / quantize) * quantize
    return int(round(beats * ppq))


def _load(path: str | Path) -> mido.MidiFile:
    try:
        return mido.MidiFile(path, charset="utf-8")
    except UnicodeDecodeError:
        return mido.MidiFile(path, charset="latin1")  # a file whose text predates anything else


def _tempo_map(mid: mido.MidiFile) -> list[tuple[int, int]]:
    """(tick, microseconds per beat) of every tempo the file sets, in order."""
    points: list[tuple[int, int]] = []
    tick = 0
    for message in mido.merge_tracks(mid.tracks):
        tick += message.time
        if message.type == "set_tempo":
            points.append((tick, message.tempo))
    if not points or points[0][0] != 0:
        return [(0, mido.bpm2tempo(120.0)), *points]  # MIDI's own default, for the opening stretch
    return points


def _seconds(tempos: list[tuple[int, int]], tick: int, ppq: int) -> float:
    """Seconds from the file's origin to `tick`, through every tempo change in between."""
    seconds = 0.0
    last, tempo = tempos[0]
    for point, value in tempos[1:]:
        if point >= tick:
            break
        seconds += (point - last) * tempo / 1e6 / ppq
        last, tempo = point, value
    return seconds + (tick - last) * tempo / 1e6 / ppq


def _bpm(tempos: list[tuple[int, int]]) -> float:
    return round(mido.tempo2bpm(tempos[0][1]), 1)


def _before(values: dict[int, int], tick: int, default: int) -> int:
    """What a controller or a program was set to just before `tick`."""
    earlier = [point for point in values if point <= tick]
    return values[max(earlier)] if earlier else default


def _save(path: str | Path, mid: mido.MidiFile) -> Path:
    """Write the file in one go, the way a project or a settings file is written."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(dir=target.parent, prefix=f"{target.name}.", suffix=".tmp")
    try:
        os.close(handle)
        mid.save(name)
        os.replace(name, target)
    except BaseException:
        Path(name).unlink(missing_ok=True)
        raise
    return target
