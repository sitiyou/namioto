# SPDX-License-Identifier: AGPL-3.0-only
"""Validated lyric input from a saved Namioto project or matched KRC and MIDI."""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from pathlib import Path

from namioto import midi, project
from namioto import settings as store
from namioto.karaoke.export_settings import ExportSettings
from namioto.karaoke.song import Song, TemplateSong, prepare_song
from namioto.karaoke.sounds import natural_sounds
from namioto.lyric_colors import DEFAULT_COLORS
from namioto.lyricmap import constraints, faithful
from namioto.lyricmap.channels import split
from namioto.lyricmap.notes import TimedNote, resolve
from namioto.lyricmap.verify import faithful_gate

ASS_FIELDS = next(section.fields for section in store.SECTIONS if section.name == "ass")


def validate_settings(settings: ExportSettings) -> None:
    settings.vocal_roles()
    for field in ASS_FIELDS:
        value = getattr(settings, field.name)
        if field.name == "channel_colors":
            settings.channel_colours()
            continue
        if field.kind in ("int", "float"):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"{field.name} must be a finite number")
            if field.kind == "int" and value != int(value):
                raise ValueError(f"{field.name} must be a whole number")
            if not field.low <= value <= field.high:
                raise ValueError(f"{field.name} must be between {field.low:g} and {field.high:g}")
        elif field.kind == "bool":
            if type(value) is not bool:
                raise ValueError(f"{field.name} must be boolean")
        elif field.kind == "choice" and value not in field.choices:
            raise ValueError(f"{field.name} must be one of {field.choices}")
        elif not isinstance(value, str):
            raise ValueError(f"{field.name} must be text")
        if field.name.endswith("_color"):
            if not value and field.name in ("overlay_blur_color", "base_blur_color", "cue_color", "ruby_color"):
                continue
            colour = value.lstrip("#")
            if len(colour) != 6 or any(char not in "0123456789abcdefABCDEF" for char in colour):
                raise ValueError(f"{field.name} must be a six-digit RGB colour")
    if -1 < settings.cue_gap < 0:
        raise ValueError("cue_gap must be -1 (automatic) or nonnegative")
    if not settings.font or any(char in settings.font for char in ",\n\r"):
        raise ValueError("font must not be empty or contain commas or line breaks")
    if settings.fade_in_ms > settings.lead_time_ms:
        raise ValueError("fade_in_ms must not exceed lead_time_ms")


def build_song(
    krc: str, data: bytes, *, settings: ExportSettings | None = None, wavetone: bool = False, semantic: bool = False
) -> Song | TemplateSong:
    imported = midi.read_bytes(data, wavetone=wavetone)
    if imported.dropped or imported.left_out:
        raise ValueError("the MIDI contains incomplete notes or unsupported channels")
    return _build_note_song(krc, imported.notes, imported.bpm, settings=settings, semantic=semantic)


def load_project_input(
    path: str | Path, *, offset_ms: int = 0, harmony_tracks: str = ""
) -> TemplateSong:
    """Read saved KRC and note times without opening the project's audio."""
    source = project.load(path)
    if source.lyrics is None:
        raise ValueError("the project has no lyrics")
    colors = {
        channel: getattr(source.settings.lyric_colors, f"channel_{channel}").lstrip("#")
        for channel in range(len(DEFAULT_COLORS))
    }
    settings = ExportSettings(offset_ms=offset_ms, harmony_tracks=harmony_tracks, channel_colors=colors)
    song = _build_note_song(
        source.lyrics.text, source.notes, source.settings.tempo.bpm, settings=settings, semantic=True,
    )
    assert isinstance(song, TemplateSong)
    return song


def _build_note_song(
    krc: str,
    notes: Sequence[project.Note],
    bpm: float,
    *,
    settings: ExportSettings | None,
    semantic: bool,
) -> Song | TemplateSong:
    settings = settings or ExportSettings()
    if semantic:
        settings = ExportSettings(offset_ms=settings.offset_ms, track_style=settings.vocal_roles(),
                                  channel_colors=settings.channel_colors)
    validate_settings(settings)
    lines = natural_sounds(krc)
    if not lines or not any(line.sounds for line in lines):
        raise ValueError("the lyrics have no sounds")
    spans = [[None] * len(line.sounds) for line in lines]
    for channel, part in split(krc).items():
        text = part.text
        resolved_notes = resolve(
            (
                TimedNote(note.start, note.start + note.duration, note.pitch, index)
                for index, note in enumerate(notes)
                if note.channel == channel
            ),
            include_branches=False,
        )
        rows = faithful.read(text, resolved_notes.stream)
        dropped = tuple(ref for ref, (_run, slots) in constraints.read(text).items() if slots == 0)
        gate = faithful_gate(
            rows,
            resolved_notes.stream,
            filtered=resolved_notes.filtered,
            subtitle=True,
            dropped=dropped,
        )
        if not gate.open():
            raise ValueError(f"channel {channel}: " + "; ".join(problem.message for problem in gate.problems))
        for refs, row in zip(part.refs, rows, strict=True):
            for ref, span in zip(refs, row, strict=True):
                spans[ref.line][ref.index] = span
    return prepare_song(krc, spans, settings, bpm, semantic=semantic)


_CHANNELS = {str(channel) for channel in range(16)}


def _channel_map(value: object, field: str) -> dict[int, str]:
    if not isinstance(value, dict):
        raise ValueError(f"song.json: {field} must be an object")
    channels = {}
    for key, label in value.items():
        if not isinstance(key, str) or key not in _CHANNELS:
            raise ValueError(f"song.json: {field} must use MIDI channels 0..15")
        if not isinstance(label, str):
            raise ValueError(f"song.json: {field} values must be strings")
        channels[int(key)] = label
    return channels


def read_song_settings(directory: str | Path) -> tuple[bool, ExportSettings]:
    """Read the MIDI convention and rendering settings in song.json."""
    data: object = json.loads((Path(directory) / "song.json").read_text(encoding="utf-8"))
    fields = {"wavetone", "offset_ms", "track_style", "colors"}
    if not isinstance(data, dict) or data.keys() != fields:
        raise ValueError("song.json must contain wavetone, offset_ms, track_style and colors")
    wavetone = data["wavetone"]
    offset_ms = data["offset_ms"]
    if type(wavetone) is not bool or type(offset_ms) is not int:
        raise ValueError("song.json: wavetone must be boolean and offset_ms must be an integer")
    return wavetone, ExportSettings(
        offset_ms=offset_ms,
        track_style=_channel_map(data["track_style"], "track_style"),
        channel_colors=_channel_map(data["colors"], "colors"),
    )


def load_song_input(directory: str | Path) -> TemplateSong:
    """Read matched KRC/MIDI with the MIDI convention, offset, roles and colours."""
    root = Path(directory)
    wavetone, settings = read_song_settings(root)
    song = build_song(
        (root / "lyrics.krc").read_text(encoding="utf-8"),
        (root / "vocal.mid").read_bytes(),
        wavetone=wavetone,
        settings=settings,
        semantic=True,
    )
    assert isinstance(song, TemplateSong)
    return song
