# SPDX-License-Identifier: AGPL-3.0-only
"""Qt-free karaoke export from canonical KRC and encoded MIDI."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path

from namioto.karaoke import ExportSettings
from namioto.karaoke.default_template import default_scene
from namioto.karaoke.output import encode_ass
from namioto.karaoke.song import Song
from namioto.karaoke.song_input import ASS_FIELDS as ASS_FIELDS
from namioto.karaoke.song_input import build_song as build_song
from namioto.karaoke.song_input import validate_settings as validate_settings
from namioto.utils import resolved, write_bytes, write_text


def check_paths(outputs: Sequence[str | Path], inputs: Sequence[str | Path] = ()) -> None:
    paths = [resolved(path) for path in outputs]
    if len(set(paths)) != len(paths) or set(paths) & {resolved(path) for path in inputs}:
        raise ValueError("output paths must be distinct and must not overwrite input files")


def build_ass(krc: str, data: bytes, *, settings: ExportSettings | None = None, wavetone: bool = False) -> str:
    song = build_song(krc, data, settings=settings, wavetone=wavetone)
    assert isinstance(song, Song)
    return encode_ass(default_scene(song, for_ass=True), quantize_clocks=True)


def export_path(path: str | Path, format: str) -> Path:
    if format not in ("ass", "hook"):
        raise ValueError(f"unknown export format {format!r}")
    target = Path(path)
    if target.suffix.lower() in (".ass", ".hook") and target.suffix.lower() != "." + format:
        raise ValueError(f"{target.name} conflicts with the selected format {format}")
    return target if target.suffix.lower() == "." + format else target.with_name(target.name + "." + format)


def build_template_scene(
    recipe, krc, data, *, settings=None, parameters=None, wavetone=False, project_colors=None, format="hook",
):
    """Resolve the same song and parameter snapshot for fragment and whole-song exports."""
    settings = settings or ExportSettings()
    values = recipe.resolve(parameters or {}, settings)
    if recipe.id == "default":
        settings = replace(settings, **values)
    if recipe.semantic:
        settings = replace(settings, channel_colors=project_colors or {})
    song = build_song(krc, data, settings=settings, wavetone=wavetone, semantic=recipe.semantic)
    if recipe.id == "default" and format == "ass":
        assert isinstance(song, Song)
        return default_scene(song, for_ass=True)
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
                                     project_colors=project_colors, format=format)
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=target.parent, suffix=target.suffix, delete=False) as stream:
            temp = Path(stream.name)
        try:
            if format == "hook":
                scene.export(temp, fps=fps)
            else:
                temp.write_text(encode_ass(scene, quantize_clocks=recipe.id == "default"), encoding="utf-8")
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
