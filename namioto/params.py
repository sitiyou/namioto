# SPDX-License-Identifier: AGPL-3.0-only
"""The primitive behind every parameter table: one `Field` per value, and the checks on it.

Qt-free on purpose. `namioto.settings` builds its spec from a table of these, and the align and
transcription windows describe their own parameter files with the same `Field`, so a value is read
back, checked and written out by one implementation everywhere.

`coerce` brings a bad value back in line: a wrong type falls to the field's default, an out-of-range
one is clamped, and a number is snapped to its step. `build_types` turns a table of `Section`s into
the dataclass the rest of the program uses.
"""

from __future__ import annotations

import json
import warnings
from collections.abc import Sequence
from dataclasses import dataclass, field, make_dataclass
from pathlib import Path
from typing import Any

from namioto.utils import write_json

TEXT_LIMIT = 4096


@dataclass(frozen=True)
class Field:
    """One setting: its default, how to read it back, and how the dialog shows it."""

    name: str
    kind: str  # bool, int, float, choice, device, text, style or secret
    default: Any
    caption: str
    tooltip: str = ""
    low: float = 0.0
    high: float = 0.0
    step: float = 0.0  # values snap to it, 0 meaning they do not
    decimals: int = 3
    choices: tuple = ()
    labels: tuple[str, ...] = ()  # what to show for each choice, the choices themselves when empty
    suffix: str = ""
    advanced: bool = False
    hidden: bool = False  # the program fills it in itself, so it never gets a row
    remembered: bool = True  # False: it belongs to one song, so the file keeps the default
    scope: str = "app"  # "project": the value describes a document, so the project file owns it


@dataclass(frozen=True)
class Section:
    name: str
    page: str
    title: str
    fields: tuple[Field, ...]


def _python_type(kind: str) -> type:
    return {"bool": bool, "int": int, "float": float}.get(kind, str)


def build_types(version: int, sections: Sequence[Section]) -> type:
    made = {
        section.name: make_dataclass(
            section.title,
            [(item.name, _python_type(item.kind), field(default=item.default)) for item in section.fields],
        )
        for section in sections
    }
    return make_dataclass(
        "Settings",
        [("version", int, field(default=version))]
        + [(section.name, made[section.name], field(default_factory=made[section.name])) for section in sections],
    )


def coerce(spec: Field, value: Any) -> Any:
    """A file may hold anything at all, so every value is checked before it is used."""
    if spec.kind == "bool":
        return value if isinstance(value, bool) else spec.default
    if spec.kind in ("choice", "device"):
        # a bool would pass for 0 or 1 here, and the synthesiser would be sent a true instead of a number
        return value if not isinstance(value, bool) and value in spec.choices else spec.default
    if spec.kind in ("int", "float"):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return spec.default
        number = min(spec.high, max(spec.low, float(value)))
        number = round(number / spec.step) * spec.step if spec.step else number
        return int(round(number)) if spec.kind == "int" else round(number, spec.decimals)
    if value is None:  # a null is a missing text, not the word "None"
        return spec.default
    text = str(value).strip()[:TEXT_LIMIT]
    return text


def defaults(fields: Sequence[Field]) -> dict[str, Any]:
    """The values a field table starts from."""
    return {item.name: item.default for item in fields}


def coerce_values(fields: Sequence[Field], values: Any) -> dict[str, Any]:
    """A file may hold anything at all, so every value goes through its field's own check."""
    if not isinstance(values, dict):
        return defaults(fields)
    return {item.name: coerce(item, values.get(item.name, item.default)) for item in fields}


def load_values(path: str | Path, fields: Sequence[Field]) -> dict[str, Any]:
    """What a dialog opens with: the file's values, or the defaults for anything unreadable."""
    target = Path(path)
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return defaults(fields)
    except (OSError, ValueError) as error:
        warnings.warn(f"{target} could not be read ({error}); defaults are in use", stacklevel=2)
        return defaults(fields)
    return coerce_values(fields, data)


def save_values(path: str | Path, fields: Sequence[Field], values: Any) -> Path:
    """Write one field table's values out whole, checked first."""
    return write_json(coerce_values(fields, values), path)
