# SPDX-License-Identifier: AGPL-3.0-only
"""The window's own state between runs: where it sat, the last folder a file came from, and the
values worth carrying from the last project to the next one.

Qt-free on purpose. No preference lives here - a value the user can choose on is in
`namioto.settings` - and nothing here is a song's own: `project` is only the memory of what the last
document held, and `namioto.project` decides which fields are kept and checks them when it reads
them back. The window writes this file as it goes, so it is not made to be edited by hand.
"""

from __future__ import annotations

import json
import os
import warnings
from dataclasses import dataclass, field
from pathlib import Path

from namioto.params import TEXT_LIMIT
from namioto.utils import config_dir, write_json


@dataclass
class State:
    geometry: str = ""
    last_audio_dir: str = ""
    project: dict[str, dict] = field(default_factory=dict)


def default_path() -> Path:
    """`$NAMIOTO_STATE`, else the platform's config directory, beside the preferences."""
    from_env = os.environ.get("NAMIOTO_STATE")
    if from_env:
        return Path(from_env)
    return config_dir("state.json")


def _text(value) -> str:
    return str(value).strip()[:TEXT_LIMIT] if isinstance(value, str) else ""


def _project(value) -> dict[str, dict]:
    """The remembered project values: a section map of charts, anything else dropped."""
    if not isinstance(value, dict):
        return {}
    return {name: values for name, values in value.items() if isinstance(values, dict)}


def from_dict(data) -> State:
    if not isinstance(data, dict):
        return State()
    return State(
        geometry=_text(data.get("geometry")),
        last_audio_dir=_text(data.get("last_audio_dir")),
        project=_project(data.get("project")),
    )


def load(path: str | Path | None = None) -> State:
    """Read the window's state, falling back to the defaults for anything missing or unusable."""
    target = Path(path) if path is not None else default_path()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return State()
    except (OSError, ValueError) as error:
        warnings.warn(f"{target} could not be read as window state ({error}); defaults are in use", stacklevel=2)
        return State()
    return from_dict(data)


def save(state: State, path: str | Path | None = None) -> Path:
    """Write the window's state out whole: a half-written file would be read as a broken one."""
    data = {"geometry": state.geometry, "last_audio_dir": state.last_audio_dir, "project": state.project}
    return write_json(data, Path(path) if path is not None else default_path())
