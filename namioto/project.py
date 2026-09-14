# SPDX-License-Identifier: AGPL-3.0-only
"""The project file: the notes, the audio they were drawn over, and the values that go with them.

Plain UTF-8 JSON, so a project can be read, diffed and edited by hand; the only thing that would need
a binary container is the spectrum, and reanalysing a five-minute file takes about a second.

Qt-free on purpose. Notes are stored in seconds and rounded to a tenth of a millisecond: seconds are
what the editor anchors them to, so a different tempo moves the grid, not the notes.
"""

from __future__ import annotations

import json
import math
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, NamedTuple

from namioto import settings as store

FORMAT = "namioto"
VERSION = 1
SUFFIX = ".nto"
NOTE_DECIMALS = 4


class Note(NamedTuple):
    """A note as the file holds it: when it starts and how long it lasts in seconds, and its pitch."""

    start: float
    duration: float
    pitch: int


@dataclass(frozen=True)
class Project:
    values: dict[str, dict] = field(default_factory=dict)  # the project-scoped settings, by section
    audio: str = ""
    notes: tuple[Note, ...] = ()


def looks_like_project(path: str | Path) -> bool:
    return Path(path).suffix.lower() == SUFFIX


def resolve_audio(project_path: str | Path, stored: str) -> Path | None:
    """Where a project's audio is: beside the project file first, then where it was saved from."""
    if not stored:
        return None
    path = Path(stored).expanduser()
    return path if path.is_absolute() else Path(project_path).parent / path


def store_audio(project_path: str | Path, audio: str | Path | None) -> str:
    """How a project should record its audio: relative to the project when that still finds it."""
    if not audio:
        return ""
    target = Path(audio).expanduser().resolve()
    try:
        return str(target.relative_to(Path(project_path).expanduser().resolve().parent))
    except ValueError:
        return str(target)  # another drive or an unrelated tree: the absolute path still works


def to_dict(project: Project) -> dict:
    return {
        "format": FORMAT,
        "version": VERSION,
        **project.values,
        "audio": project.audio,
        "notes": [
            {
                "start": round(note.start, NOTE_DECIMALS),
                "duration": round(note.duration, NOTE_DECIMALS),
                "pitch": note.pitch,
            }
            for note in project.notes
        ],
    }


def _audio(value: Any) -> str:
    return str(value).strip()[: store.TEXT_LIMIT] if isinstance(value, str) else ""


def _note(entry: Any) -> Note | None:
    if not isinstance(entry, dict):
        return None
    start, duration, pitch = entry.get("start"), entry.get("duration"), entry.get("pitch")
    numbers = (start, duration, pitch)
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in numbers):
        return None
    if not all(math.isfinite(value) for value in numbers) or duration <= 0:
        return None
    return Note(max(0.0, float(start)), float(duration), int(round(pitch)))


def _notes(value: Any) -> tuple[Note, ...]:
    if not isinstance(value, list):
        return ()
    notes: list[Note] = []
    dropped = 0
    for entry in value:
        note = _note(entry)
        if note is None:
            dropped += 1
        else:
            notes.append(note)
    if dropped:
        warnings.warn(f"{dropped} of the notes in the project could not be read and were left out", stacklevel=3)
    return tuple(notes)


def from_dict(data: Any) -> Project:
    """Read a project out of a parsed file, with every value checked the way the settings are."""
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise ValueError(f"not a {FORMAT} project")
    values = store.Settings()
    store.apply_project_values(values, data)
    return Project(
        values=store.project_values(values), audio=_audio(data.get("audio")), notes=_notes(data.get("notes"))
    )


def load(path: str | Path) -> Project:
    """Read a project. Raises OSError or ValueError, both of which say what was wrong."""
    return from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


def save(project: Project, path: str | Path) -> Path:
    """Write the project out whole: a half-written file would be read as a broken one."""
    return store.write_json(to_dict(project), Path(path))
