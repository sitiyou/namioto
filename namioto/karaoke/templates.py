"""Registration and typed parameter resolution for explicitly loaded Python effects."""

import hashlib
import json
import math
import runpy
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from kara_templater import Color, Scene

from namioto.karaoke.default_template import default_scene
from namioto.karaoke.export_settings import ExportSettings
from namioto.karaoke.song import Song
from namioto.params import Field
from namioto.utils import write_json


def parameter_value(field: Field, value):
    if field.kind == "color":
        Color.hex("#" + str(value).lstrip("#"))
        return str(value).lstrip("#")
    if field.kind == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.lower() in ("true", "false"):
            return value.lower() == "true"
        raise ValueError("expected true or false")
    if field.kind in ("int", "float"):
        if isinstance(value, bool):
            raise ValueError("expected a number")
        number = float(value)
        if not math.isfinite(number) or not field.low <= number <= field.high:
            raise ValueError(f"expected {field.low:g}..{field.high:g}")
        if field.kind == "int" and not number.is_integer():
            raise ValueError("expected an integer")
        return int(number) if field.kind == "int" else number
    if field.kind == "choice" and value not in field.choices:
        raise ValueError(f"expected one of {field.choices}")
    if field.kind not in ("text", "choice"):
        raise ValueError(f"unknown parameter kind {field.kind!r}")
    if not isinstance(value, str):
        raise ValueError("expected text")
    return value


@dataclass(frozen=True)
class Template:
    id: str
    title: str
    formats: tuple[str, ...]
    parameters: tuple[Field, ...]
    build: Callable[[Song, dict], Scene]
    validate: Callable[[dict], None] | None = None

    def resolve(self, overrides: Mapping, settings: ExportSettings | None = None) -> dict:
        result = {}
        unknown = set(overrides).difference(field.name for field in self.parameters)
        if unknown:
            raise ValueError(f"{self.id}: unknown parameters {sorted(unknown)}")
        for field in self.parameters:
            default = getattr(settings, field.name, field.default) if self.id == "default" else field.default
            value = overrides.get(field.name, default)
            if field.name == "channel_colors" and self.id == "default" and isinstance(value, Mapping):
                result[field.name] = value
                continue
            try:
                result[field.name] = parameter_value(field, value)
            except (TypeError, ValueError) as error:
                raise ValueError(f"{self.id}.{field.name}: {error}") from error
        if self.validate is not None:
            try:
                self.validate(result)
            except ValueError as error:
                raise ValueError(f"{self.id}: {error}") from error
        return result


class Registry:
    def __init__(self):
        self.templates: dict[str, Template] = {}
        self.origins: dict[str, str] = {}
        self.source = "built-in"

    def add(self, *, id, title, build, formats=("ass", "hook"), parameters=(), validate=None):
        if not id or id in self.templates:
            raise ValueError(f"duplicate or empty template {id!r}: {self.origins.get(id)} and {self.source}")
        fields = tuple(parameters)
        if len({f.name for f in fields}) != len(fields) or not callable(build):
            raise ValueError(f"{id}: invalid parameters or builder")
        if not formats or set(formats) - {"ass", "hook"}:
            raise ValueError(f"{id}: invalid formats")
        template = Template(id, title, tuple(formats), fields, build, validate)
        template.resolve({})
        self.templates[id] = template
        self.origins[id] = self.source

    def get(self, id):
        if id not in self.templates:
            raise ValueError(f"unknown template {id!r}; load its Python script")
        return self.templates[id]


def load_templates(scripts: Iterable[str | Path] = ()) -> Registry:
    from namioto.subtitle import ASS_FIELDS

    registry = Registry()
    registry.add(id="default", title="Default", parameters=ASS_FIELDS, build=lambda song, _: default_scene(song))
    for path in map(Path, scripts):
        if path.suffix.lower() != ".py":
            raise ValueError(f"{path}: expected a Python template file (.py)")
        registry.source = str(path)
        try:
            namespace = runpy.run_path(str(path))
            register = namespace.get("register")
            if not callable(register):
                raise ValueError("expected register(registry)")
            register(registry)
        except Exception as error:
            raise ValueError(f"{path}: {error}") from error
    return registry


def preference_path() -> Path:
    from namioto.settings import default_path

    path = default_path()
    return path.with_name(path.stem + "-export.json")


def project_scripts(project: Path | None) -> tuple[Path, ...]:
    """Find project effects by name without importing any Python."""
    if project is None:
        return ()
    root = project.absolute().parent
    return tuple(path for path in (root / "template.py", *sorted((root / "template").glob("*.py"))) if path.is_file())


def _project_preferences(project: Path) -> Path:
    key = hashlib.sha256(str(project.resolve()).encode()).hexdigest()
    return preference_path().with_name(f"export-{key}.json")


def read_preferences(project: Path | None = None) -> dict:
    """Read editor-local recipe choices. Song files never supply executable paths."""
    try:
        path = _project_preferences(project) if project is not None else preference_path()
        if project is not None and not path.is_file():
            return read_preferences()
        data = json.loads(path.read_text(encoding="utf-8"))
        if project is not None and isinstance(data, dict):
            global_choices = read_preferences()
            data.update({key: global_choices[key] for key in ("format", "fps") if key in global_choices})
    except OSError, ValueError:
        return {}
    if not isinstance(data, dict):
        return {}
    scripts = data.get("scripts", [])
    values = data.get("values", {})
    fps = data.get("fps", 60)
    return {
        "scripts": [s for s in scripts if isinstance(s, str)] if isinstance(scripts, list) else [],
        "values": {k: v for k, v in values.items() if isinstance(v, dict)} if isinstance(values, dict) else {},
        "id": data.get("id") if isinstance(data.get("id"), str) else "default",
        "format": "hook" if data.get("format") == "hook" else "ass",
        "fps": fps if type(fps) is int and 1 <= fps <= 240 else 60,
    }


def save_preferences(preferences: dict, project: Path | None = None) -> None:
    if project is not None:
        local = {str(path) for path in project_scripts(project)}
        if local.intersection(preferences["scripts"]):
            write_json(preferences, _project_preferences(project))
            global_choices = read_preferences()
            write_json(
                {**global_choices, "format": preferences["format"], "fps": preferences["fps"]}, preference_path()
            )
            return
        _project_preferences(project).unlink(missing_ok=True)
    write_json(preferences, preference_path())
