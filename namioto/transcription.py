# SPDX-License-Identifier: AGPL-3.0-only
"""GAME transcription as the editor uses it: the run's parameters, their store, and the child entry.

Qt-free on purpose, and free of `namioto.game` too: importing that would pull `onnxruntime` into the
GUI's startup path. `namioto.ui.transcription_dialog` builds its form from `PARAMETERS`, and
`transcribe` is the module-level function a spawned process runs, so a crash cannot touch the GUI.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import time
import traceback
import warnings
from typing import Any

import platformdirs

from namioto import settings as store
from namioto.settings import Field

GAME_SIZES = ("small", "medium", "large")
TARGETS = ("new", "active", "replace")
PARAMETER_FILE = "transcription.json"
STORE_VERSION = 1

PARAMETERS: tuple[Field, ...] = (
    Field(
        "size",
        "choice",
        "small",
        "Model",
        "GAME's ONNX model, downloaded into the data directory on first use",
        choices=GAME_SIZES,
    ),
    Field(
        "language",
        "text",
        "",
        "Language",
        "Language code the model knows (zh, ja, ...); empty uses the model's own default",
    ),
    Field(
        "quantize",
        "choice",
        0,
        "Quantize",
        "Snap the notes to the beat grid, this many cells per quarter note",
        choices=(0, 1, 2, 4, 8, 16, 32),
        labels=("Off", "1/4", "1/8", "1/16", "1/32", "1/64", "1/128"),
    ),
    Field("fit_tempo", "bool", False, "Fit tempo", "Refine the grid period from the detected onsets"),
    Field(
        "target",
        "choice",
        "new",
        "Target",
        "Where the notes land: a channel of their own, the active channel, or a fresh start",
        choices=TARGETS,
        labels=("New channel", "Active channel", "Replace all notes"),
    ),
    Field("batch_size", "int", 4, "Batch", "Chunks per inference batch", low=1, high=32, advanced=True),
    Field(
        "seg_threshold",
        "float",
        0.2,
        "Segment threshold",
        "Boundary decoding threshold",
        low=0.0,
        high=1.0,
        step=0.05,
        decimals=2,
        advanced=True,
    ),
    Field(
        "seg_radius",
        "float",
        0.02,
        "Segment radius",
        "Boundary decoding radius",
        suffix="s",
        low=0.005,
        high=0.2,
        step=0.005,
        decimals=3,
        advanced=True,
    ),
    Field(
        "est_threshold",
        "float",
        0.2,
        "Estimate threshold",
        "Note presence threshold",
        low=0.0,
        high=1.0,
        step=0.05,
        decimals=2,
        advanced=True,
    ),
    Field(
        "d3pm_t0",
        "float",
        0.0,
        "D3PM T0",
        "Starting T of D3PM sampling",
        low=0.0,
        high=1.0,
        step=0.05,
        decimals=2,
        advanced=True,
    ),
    Field("d3pm_steps", "int", 8, "D3PM steps", "Number of D3PM sampling steps", low=1, high=64, advanced=True),
    Field(
        "silence_slice",
        "bool",
        True,
        "Silence slicing",
        "Cut the audio at its silences before inference",
        advanced=True,
    ),
)


def default_parameters() -> dict[str, Any]:
    return {item.name: item.default for item in PARAMETERS}


def coerce_parameters(values: Any) -> dict[str, Any]:
    """A file may hold anything at all, so every value goes through its field's own check."""
    if not isinstance(values, dict):
        return default_parameters()
    return {item.name: store.coerce(item, values.get(item.name, item.default)) for item in PARAMETERS}


def parameter_path() -> pathlib.Path:
    """The preferences side of the tree: the dialog's remembered values sit next to settings.json."""
    return pathlib.Path(platformdirs.user_config_dir("namioto")) / PARAMETER_FILE


def results_root() -> pathlib.Path:
    """The data side, where the models already live: transcriptions are derived, not preferences."""
    return pathlib.Path(platformdirs.user_data_dir("namioto")) / "transcriptions"


def load_parameters() -> dict[str, Any]:
    """What the dialog opens with: the last run's values, or the defaults for anything unreadable."""
    try:
        data = json.loads(parameter_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default_parameters()
    except (OSError, ValueError) as error:
        warnings.warn(f"{parameter_path()} could not be read ({error}); defaults are in use", stacklevel=2)
        return default_parameters()
    return coerce_parameters(data)


def save_parameters(values: Any) -> pathlib.Path:
    return store.write_json(coerce_parameters(values), parameter_path())


def _resolved(path: Any) -> pathlib.Path:
    return pathlib.Path(path).expanduser().resolve()


def _audio_stamp(path: Any) -> tuple[int, int]:
    """Size and mtime, so the same path holding different audio is not the same audio."""
    try:
        info = _resolved(path).stat()
    except OSError:
        return (0, 0)
    return (info.st_size, info.st_mtime_ns)


def audio_key(path: Any) -> str:
    return hashlib.sha1(str(_resolved(path)).encode("utf-8")).hexdigest()


def run_key(path: Any, parameters: Any, tempo: float) -> str:
    """What makes two runs the same one: the audio, the run's inputs, and a quantised run's grid.

    Where the notes are put afterwards is not one of them: the same transcription can be inserted
    somewhere else without asking the model again.
    """
    values = coerce_parameters(parameters)
    identity = {
        "audio": list(_audio_stamp(path)),
        "parameters": {item.name: values[item.name] for item in PARAMETERS if item.name != "target"},
        "tempo": round(float(tempo), 6) if values["quantize"] else None,
    }
    return hashlib.sha1(json.dumps(identity, sort_keys=True).encode("utf-8")).hexdigest()


def _results_path(path: Any) -> pathlib.Path:
    return results_root() / f"{audio_key(path)}.json"


def _notes(run: dict) -> list[tuple[float, float, float]] | None:
    """The notes of one stored run, or None when the record is not one that can be used."""
    notes = run.get("notes")
    if not isinstance(notes, list):
        return None
    try:
        return [(float(onset), float(offset), float(pitch)) for onset, offset, pitch in notes]
    except (TypeError, ValueError):
        return None


def load_runs(path: Any) -> dict[str, dict]:
    """Every run saved for one audio file, keyed by run key; an unreadable file means no runs."""
    try:
        data = json.loads(_results_path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    runs = data.get("runs") if isinstance(data, dict) else None
    if not isinstance(runs, dict):
        return {}
    return {key: run for key, run in runs.items() if isinstance(run, dict) and _notes(run) is not None}


def find_run(path: Any, parameters: Any, tempo: float) -> list[tuple[float, float, float]] | None:
    """The notes a run with these exact inputs already found, or None when there is no such run."""
    found = load_runs(path).get(run_key(path, parameters, tempo))
    return _notes(found) if found else None


def save_run(path: Any, parameters: Any, tempo: float, notes) -> pathlib.Path:
    """Keep one entry per run key: running the same thing again replaces what it found last time."""
    runs = load_runs(path)
    runs[run_key(path, parameters, tempo)] = {
        "at": int(time.time()),
        "parameters": coerce_parameters(parameters),
        "notes": [
            [round(float(onset), 4), round(float(offset), 4), round(float(pitch), 4)] for onset, offset, pitch in notes
        ],
    }
    payload = {"version": STORE_VERSION, "audio": str(_resolved(path)), "runs": runs}
    return store.write_json(payload, _results_path(path))


def transcribe(path: str, parameters: dict, tempo: float, queue) -> None:
    """Run GAME for one audio file, reporting through `queue`: a spawned process's whole job.

    The messages are `("log", text)`, `("progress", stage, done, total)`, `("done", notes)` and
    `("error", traceback)`. Nothing is written to disk here: the parent saves what comes back, so a
    crash cannot leave a half-written result behind.
    """
    values = coerce_parameters(parameters)
    try:
        from namioto import game

        queue.put(("log", f"GAME model {values['size']}"))
        model = game.resolve_model(
            size=values["size"],
            progress=lambda done, total: queue.put(("progress", "download", done, total)),
        )
        queue.put(("log", f"model at {model}"))
        backend = game.OnnxBackend(model)
        notes = game.extract(
            backend,
            path,
            language=values["language"] or None,
            batch_size=values["batch_size"],
            seg_threshold=values["seg_threshold"],
            seg_radius=values["seg_radius"],
            est_threshold=values["est_threshold"],
            d3pm_t0=values["d3pm_t0"],
            d3pm_steps=values["d3pm_steps"],
            silence_slice=values["silence_slice"],
            progress=lambda done, total: queue.put(("progress", "parts", done, total)),
        )
        if values["quantize"]:
            notes, unit, phase = game.quantized(notes, tempo, values["quantize"], fit_tempo=values["fit_tempo"])
            queue.put(("log", f"grid {unit * 1000:.1f} ms, phase {phase * 1000:.1f} ms"))
        queue.put(("log", f"{len(notes)} notes"))
        queue.put(("done", notes))
    except Exception:  # the child must report a crash rather than take the queue down with it
        queue.put(("error", traceback.format_exc()))
