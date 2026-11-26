# SPDX-License-Identifier: AGPL-3.0-only
"""Qt-free ASS export from validated, line-aligned lyrics."""

from collections.abc import Sequence
from pathlib import Path

from namioto.karaoke import ExportSettings
from namioto.karaoke.ass import write_ass
from namioto.karaoke.song_input import build_song, load_project_input
from namioto.utils import resolved, write_bytes, write_text


def check_paths(outputs: Sequence[str | Path], inputs: Sequence[str | Path] = ()) -> None:
    paths = [resolved(path) for path in outputs]
    if len(set(paths)) != len(paths) or set(paths) & {resolved(path) for path in inputs}:
        raise ValueError("output paths must be distinct and must not overwrite input files")


def build_ass(krc: str, data: bytes, *, settings: ExportSettings | None = None, wavetone: bool = False) -> str:
    song = build_song(krc, data, wavetone=wavetone)
    return write_ass(song.lines, song.spans, settings or ExportSettings())


def export_path(path: str | Path) -> Path:
    target = Path(path)
    if target.suffix.lower() == ".hook":
        raise ValueError("subtitle output must use .ass")
    return target if target.suffix.lower() == ".ass" else target.with_name(target.name + ".ass")


def export_project(path: str | Path, source: str | Path, *, settings: ExportSettings | None = None) -> Path:
    """Write an ASS from a saved project without opening its audio or rounding through MIDI."""
    target = export_path(path)
    check_paths([target], [source])
    song = load_project_input(source)
    return write_text(target, write_ass(song.lines, song.spans, settings or ExportSettings()))


def export_song(
    path: str | Path,
    krc: str,
    data: bytes,
    *,
    settings: ExportSettings | None = None,
    wavetone: bool = False,
    save_krc: str | Path | None = None,
    save_midi: str | Path | None = None,
) -> Path:
    """Build the subtitle before writing files, then atomically replace the ASS."""
    target = export_path(path)
    outputs = [target, *(p for p in (save_krc, save_midi) if p is not None)]
    check_paths(outputs)
    ass = build_ass(krc, data, settings=settings, wavetone=wavetone)
    if save_krc is not None:
        write_text(save_krc, krc)
    if save_midi is not None:
        write_bytes(save_midi, data)
    return write_text(target, ass)
