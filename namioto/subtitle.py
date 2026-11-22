# SPDX-License-Identifier: AGPL-3.0-only
"""Qt-free karaoke export from canonical KRC and encoded MIDI, shared by every entry point.

KRC channel wrappers select zero-based MIDI channels. Their NOTE slots are read faithfully after MIDI
serialization, so persistent and in-memory intermediate exports have identical timing.
"""

from __future__ import annotations

import math
import os
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path

from namioto import midi
from namioto import settings as store
from namioto.karaoke import ExportSettings
from namioto.karaoke.default_template import default_scene
from namioto.karaoke.output import encode_ass
from namioto.karaoke.song import Song, TemplateSong, prepare_song
from namioto.karaoke.sounds import natural_sounds
from namioto.lyricmap import constraints, faithful
from namioto.lyricmap.channels import split
from namioto.lyricmap.notes import TimedNote, resolve
from namioto.lyricmap.verify import faithful_gate
from namioto.utils import resolved, write_bytes, write_text

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
    settings = settings or ExportSettings()
    if semantic:
        settings = ExportSettings(offset_ms=settings.offset_ms, track_style=settings.track_style,
                                  channel_colors=settings.channel_colors)
    validate_settings(settings)
    lines = natural_sounds(krc)
    if not lines or not any(line.sounds for line in lines):
        raise ValueError("the lyrics have no sounds")
    imported = midi.read_bytes(data, wavetone=wavetone)
    if imported.dropped or imported.left_out:
        raise ValueError("the MIDI contains incomplete notes or unsupported channels")
    spans = [[None] * len(line.sounds) for line in lines]
    for channel, part in split(krc).items():
        text = part.text
        notes = resolve(
            (
                TimedNote(note.start, note.start + note.duration, note.pitch, index)
                for index, note in enumerate(imported.notes)
                if note.channel == channel
            ),
            include_branches=False,
        )
        rows = faithful.read(text, notes.stream)
        dropped = tuple(ref for ref, (_run, slots) in constraints.read(text).items() if slots == 0)
        gate = faithful_gate(
            rows,
            notes.stream,
            filtered=notes.filtered,
            subtitle=True,
            dropped=dropped,
        )
        if not gate.open():
            raise ValueError(f"channel {channel}: " + "; ".join(problem.message for problem in gate.problems))
        for refs, row in zip(part.refs, rows, strict=True):
            for ref, span in zip(refs, row, strict=True):
                spans[ref.line][ref.index] = span
    return prepare_song(krc, spans, settings, imported.bpm, semantic=semantic)


def check_paths(outputs: Sequence[str | Path], inputs: Sequence[str | Path] = ()) -> None:
    paths = [resolved(path) for path in outputs]
    if len(set(paths)) != len(paths) or set(paths) & {resolved(path) for path in inputs}:
        raise ValueError("output paths must be distinct and must not overwrite input files")


def build_ass(krc: str, data: bytes, *, settings: ExportSettings | None = None, wavetone: bool = False) -> str:
    song = build_song(krc, data, settings=settings, wavetone=wavetone)
    return encode_ass(default_scene(song))


def export_path(path: str | Path, format: str) -> Path:
    if format not in ("ass", "hook"):
        raise ValueError(f"unknown export format {format!r}")
    target = Path(path)
    if target.suffix.lower() in (".ass", ".hook") and target.suffix.lower() != "." + format:
        raise ValueError(f"{target.name} conflicts with the selected format {format}")
    return target if target.suffix.lower() == "." + format else target.with_name(target.name + "." + format)


def build_template_scene(recipe, krc, data, *, settings=None, parameters=None, wavetone=False, project_colors=None):
    """Resolve the same song and parameter snapshot for fragment and whole-song exports."""
    settings = settings or ExportSettings()
    values = recipe.resolve(parameters or {}, settings)
    if recipe.id == "default":
        settings = replace(settings, **values)
    if recipe.semantic:
        settings = replace(settings, channel_colors=project_colors or {})
    song = build_song(krc, data, settings=settings, wavetone=wavetone, semantic=recipe.semantic)
    return recipe.build(song, values)


def export_song(
    path: str | Path,
    krc: str,
    data: bytes,
    *,
    settings: ExportSettings | None = None,
    wavetone: bool = False,
    template: str = "default",
    project_colors: Mapping[int, str] | None = None,
    parameters: Mapping | None = None,
    scripts: Sequence[str | Path] = (),
    format: str = "ass",
    fps: int = 60,
    save_krc: str | Path | None = None,
    save_midi: str | Path | None = None,
) -> Path:
    """Resolve the recipe once and replace the requested file only after compilation succeeds."""
    from namioto.karaoke.templates import load_templates

    target = export_path(path, format)
    check_paths([target, *([save_krc] if save_krc else []), *([save_midi] if save_midi else [])], scripts)
    recipe = load_templates(scripts).get(template)
    if format not in recipe.formats:
        raise ValueError(f"{template}: unsupported format {format}")
    try:
        scene = build_template_scene(recipe, krc, data, settings=settings, parameters=parameters, wavetone=wavetone,
                                     project_colors=project_colors)
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=target.parent, suffix=target.suffix, delete=False) as stream:
            temp = Path(stream.name)
        try:
            if format == "hook":
                scene.export_hook(temp, fps=fps)
            else:
                temp.write_text(encode_ass(scene), encoding="utf-8")
            if save_krc:
                write_text(save_krc, krc)
            if save_midi:
                write_bytes(save_midi, data)
            os.replace(temp, target)
        finally:
            temp.unlink(missing_ok=True)
    except ValueError, OSError:
        raise
    except Exception as error:
        raise ValueError(f"{template}: {error}") from error
    return target
