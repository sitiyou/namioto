# SPDX-License-Identifier: AGPL-3.0-only
"""What the program remembers between runs: the settings model, its file and its defaults.

Qt-free on purpose. The spec table below is the single source of truth: it gives the defaults, tells
`load` how to read a value back from the file (type, range, the values a choice may take) and lets
`namioto.ui.settings_dialog` build its pages without repeating any of it.
"""

from __future__ import annotations

import copy
import json
import os
import tempfile
import warnings
from dataclasses import dataclass, field, make_dataclass
from pathlib import Path
from typing import Any

import platformdirs

from namioto.spectrum import CHANNEL_MODES

VERSION = 1
TEXT_LIMIT = 4096
DIVISIONS = ("beats", "seconds")
BACKENDS = ("auto", "external", "builtin")
ESTIMATORS = ("beats", "tempocnn")
MODEL_SIZES = ("small", "medium", "large")
LANGUAGES = ("en", "ja", "zh")
SUBDIVISIONS = (1, 2, 4, 8, 16, 32)


@dataclass(frozen=True)
class Field:
    """One setting: its default, how to read it back, and how the dialog shows it."""

    name: str
    kind: str  # bool, int, float, choice, text or path
    default: Any
    caption: str
    tooltip: str = ""
    low: float = 0.0
    high: float = 0.0
    step: float = 0.0  # values snap to it, 0 meaning they do not
    decimals: int = 3
    choices: tuple = ()
    suffix: str = ""
    advanced: bool = False
    hidden: bool = False


@dataclass(frozen=True)
class Section:
    name: str
    page: str
    title: str
    fields: tuple[Field, ...]


SECTIONS: tuple[Section, ...] = (
    Section(
        "analysis",
        "Analysis",
        "Analysis",
        (
            Field(
                "channels",
                "choice",
                "mono",
                "Channels",
                "Which channels the analysis reads",
                choices=CHANNEL_MODES,
            ),
            Field(
                "t_num",
                "float",
                40.0,
                "Frames/s",
                "Analysis frames per second: the time resolution of the spectrum",
                low=1,
                high=200,
                decimals=2,
            ),
            Field(
                "fft_points",
                "int",
                8192,
                "FFT points",
                "Window size of the analysis: the frequency resolution",
                low=256,
                high=32768,
                step=256,
            ),
            Field(
                "a4",
                "float",
                440.0,
                "A4 (Hz)",
                "Frequency of A4, followed by both the analysis bands and the played notes",
                low=400,
                high=480,
                step=0.5,
                decimals=1,
            ),
        ),
    ),
    Section(
        "spectrum",
        "Display",
        "Spectrum",
        (
            Field("gain", "float", 240.0, "Gain", "Energy it takes for the spectrum to reach full red", 10, 600, 1),
            Field(
                "contrast",
                "float",
                1.0,
                "Contrast",
                "Exponent applied to the spectrum's energy",
                low=0.2,
                high=4.0,
                step=0.1,
                decimals=1,
            ),
            Field(
                "dim_in_edit_mode",
                "bool",
                True,
                "Dim while editing",
                "Step the spectrum back while editing, so the notes stand out over it",
            ),
        ),
    ),
    Section(
        "playback",
        "Playback",
        "Playback",
        (
            Field(
                "backend",
                "choice",
                "auto",
                "Output",
                "auto uses an external synth when one is listening, else the built-in one",
                choices=BACKENDS,
            ),
            Field(
                "midi_port",
                "text",
                "",
                "MIDI port",
                "Name of the external MIDI port; empty picks the first software synth found",
            ),
            Field(
                "buffer_ms",
                "int",
                80,
                "Buffer (ms)",
                "Audio buffer: smaller is lower latency, larger is safer against dropouts",
                low=10,
                high=1000,
                step=10,
            ),
            Field("velocity", "int", 100, "Velocity", "Note velocity, for an external synth", low=1, high=127),
            Field(
                "program",
                "int",
                0,
                "Program",
                "General MIDI program sent to an external synth; 0 is a grand piano",
                low=0,
                high=127,
            ),
            Field(
                "preview_seconds",
                "float",
                0.6,
                "Preview (s)",
                "How long a note auditioned by a click keeps sounding",
                low=0.05,
                high=5.0,
                step=0.05,
                decimals=2,
            ),
            Field(
                "audio_volume",
                "int",
                80,
                "Audio volume",
                "Starting volume of the analysed audio",
                low=0,
                high=100,
                suffix="%",
            ),
            Field(
                "midi_volume",
                "int",
                80,
                "MIDI volume",
                "Starting volume of the note playback",
                low=0,
                high=100,
                suffix="%",
            ),
            Field(
                "latency_ms",
                "int",
                0,
                "Latency (ms)",
                "Offset between the sound and the displayed waveform",
                low=-500,
                high=500,
                suffix=" ms",
            ),
            Field(
                "speed",
                "float",
                1.0,
                "Speed",
                "Playback speed in 5% steps, 0.10x to 2.00x; the pitch is left alone",
                low=0.1,
                high=2.0,
                step=0.05,
                decimals=2,
                suffix="x",
            ),
        ),
    ),
    Section(
        "editor",
        "Editor",
        "Editor",
        (
            Field(
                "start_in_edit_mode",
                "bool",
                False,
                "Start in edit mode",
                "Open with the pen rather than the view tool",
            ),
            Field("snap", "float", 0.5, "Snap", "Snap grid for the pen tool", low=0.0625, high=4.0),
            Field(
                "division",
                "choice",
                "beats",
                "Division",
                "What the ruler's lower row and the drawn grid lines divide by",
                choices=DIVISIONS,
            ),
            Field(
                "zoom_x",
                "float",
                48.0,
                "Zoom x",
                "Pixels per beat at startup",
                low=12,
                high=900,
                decimals=1,
            ),
            Field("zoom_y", "float", 16.0, "Zoom y", "Pixels per semitone row at startup", low=8, high=64, decimals=1),
            Field(
                "overtone_highlight",
                "bool",
                True,
                "Overtone highlight",
                "Paint the octave and the twelfth of the row being edited as well",
            ),
        ),
    ),
    Section(
        "tempo",
        "Tempo",
        "Tempo",
        (
            Field(
                "estimator",
                "choice",
                "beats",
                "Estimator",
                "beats: beat tracking and a least-squares fit; tempocnn: the TempoCNN model",
                choices=ESTIMATORS,
            ),
            Field(
                "bpm",
                "float",
                120.0,
                "Tempo",
                "Tempo of the beat grid at startup",
                low=20,
                high=300,
                step=0.1,
                decimals=1,
            ),
            Field(
                "window_seconds",
                "float",
                12.0,
                "Window (s)",
                "Length of the windows the local tempo is fitted to",
                low=2,
                high=120,
                step=1,
                advanced=True,
            ),
            Field(
                "window_hop_seconds",
                "float",
                6.0,
                "Hop (s)",
                "Distance between those windows",
                low=1,
                high=60,
                step=1,
                advanced=True,
            ),
        ),
    ),
    Section(
        "extraction",
        "Extraction",
        "Extraction",
        (
            Field(
                "model_size",
                "choice",
                "small",
                "Model",
                "Which of GAME's three model sizes to use; it is downloaded on first use",
                choices=MODEL_SIZES,
            ),
            Field(
                "model_dir",
                "path",
                "",
                "Model directory",
                "A directory that already holds the model; empty uses the data directory",
            ),
            Field(
                "language",
                "choice",
                "zh",
                "Language",
                "Language the model is told to expect in the singing",
                choices=LANGUAGES,
            ),
            Field("quantize", "bool", True, "Quantize", "Snap the extracted notes to the grid before inserting them"),
            Field(
                "quantize_subdivisions",
                "choice",
                4,
                "Subdivisions",
                "Notes per beat of that grid",
                choices=SUBDIVISIONS,
            ),
            Field(
                "batch_size",
                "int",
                4,
                "Batch size",
                "Chunks the model is run on at once",
                low=1,
                high=64,
                advanced=True,
            ),
            Field(
                "seg_threshold",
                "float",
                0.2,
                "Boundary threshold",
                "How sure the segmenter has to be of a note boundary",
                low=0.0,
                high=1.0,
                step=0.01,
                decimals=2,
                advanced=True,
            ),
            Field(
                "seg_radius",
                "float",
                0.02,
                "Boundary radius",
                "Seconds the boundary search may move a boundary by",
                low=0.0,
                high=1.0,
                step=0.01,
                decimals=2,
                advanced=True,
            ),
            Field(
                "est_threshold",
                "float",
                0.2,
                "Pitch threshold",
                "How sure the estimator has to be that a note sounds",
                low=0.0,
                high=1.0,
                step=0.01,
                decimals=2,
                advanced=True,
            ),
            Field(
                "d3pm_t0",
                "float",
                0.0,
                "D3PM t0",
                "Where the segmenter's diffusion starts",
                low=0.0,
                high=10.0,
                step=0.1,
                decimals=1,
                advanced=True,
            ),
            Field("d3pm_steps", "int", 8, "D3PM steps", "How many steps it takes", low=1, high=64, advanced=True),
            Field(
                "silence_slice",
                "bool",
                True,
                "Slice on silence",
                "Cut the audio on its silences before running the model",
                advanced=True,
            ),
        ),
    ),
    Section(
        "paths",
        "Advanced",
        "Paths",
        (
            Field(
                "last_audio_dir",
                "path",
                "",
                "Last audio directory",
                "Where the file chooser starts",
            ),
        ),
    ),
    Section(
        "session",
        "Advanced",
        "Session",
        (
            Field("geometry", "text", "", "Window geometry", hidden=True),
            Field("window_state", "text", "", "Window state", hidden=True),
            Field("center_x", "float", 8.0, "View center x", hidden=True, low=0.0, high=10000.0, decimals=1),
            Field("center_y", "float", 48.0, "View center y", hidden=True, low=0.0, high=88.0, decimals=1),
        ),
    ),
)


def _python_type(kind: str) -> type:
    return {"bool": bool, "int": int, "float": float}.get(kind, str)


def _build_types() -> type:
    made = {
        section.name: make_dataclass(
            section.title,
            [(item.name, _python_type(item.kind), field(default=item.default)) for item in section.fields],
        )
        for section in SECTIONS
    }
    return make_dataclass(
        "Settings",
        [("version", int, field(default=VERSION))]
        + [(section.name, made[section.name], field(default_factory=made[section.name])) for section in SECTIONS],
    )


Settings = _build_types()
FIELD_SPECS = {(section.name, item.name): item for section in SECTIONS for item in section.fields}


def default_path() -> Path:
    """`$NAMIOTO_SETTINGS`, else the platform's config directory, where preferences belong."""
    from_env = os.environ.get("NAMIOTO_SETTINGS")
    if from_env:
        return Path(from_env)
    return Path(platformdirs.user_config_dir("namioto")) / "settings.json"


def get_value(settings: Settings, section: str, name: str) -> Any:
    return getattr(getattr(settings, section), name)


def set_value(settings: Settings, section: str, name: str, value: Any) -> None:
    """Put `value` on one field, first making it fit the field's type and range."""
    setattr(getattr(settings, section), name, coerce(FIELD_SPECS[(section, name)], value))


def coerce(spec: Field, value: Any) -> Any:
    """A file may hold anything at all, so every value is checked before it is used."""
    if spec.kind == "bool":
        return value if isinstance(value, bool) else spec.default
    if spec.kind == "choice":
        return value if value in spec.choices else spec.default
    if spec.kind in ("int", "float"):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return spec.default
        number = min(spec.high, max(spec.low, float(value)))
        number = round(number / spec.step) * spec.step if spec.step else number
        return int(round(number)) if spec.kind == "int" else round(number, spec.decimals)
    text = str(value).strip()[:TEXT_LIMIT]
    return text


def to_dict(settings: Settings) -> dict:
    return {
        "version": VERSION,
        **{
            section.name: {item.name: getattr(getattr(settings, section.name), item.name) for item in section.fields}
            for section in SECTIONS
        },
    }


def from_dict(data: Any) -> Settings:
    settings = Settings()
    if not isinstance(data, dict):
        return settings
    for section in SECTIONS:
        stored = data.get(section.name)
        if isinstance(stored, dict):
            for item in section.fields:
                if item.name in stored:
                    setattr(getattr(settings, section.name), item.name, coerce(item, stored[item.name]))
    return settings


def load(path: str | Path | None = None) -> Settings:
    """Read the settings, falling back to the defaults for anything missing or unusable."""
    target = Path(path) if path is not None else default_path()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return Settings()
    except (OSError, ValueError) as error:
        warnings.warn(f"{target} could not be read as settings ({error}); defaults are in use", stacklevel=2)
        return Settings()
    return from_dict(data)


def save(settings: Settings, path: str | Path | None = None) -> Path:
    """Write the settings out whole: a half-written file would be read as a broken one."""
    target = Path(path) if path is not None else default_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(to_dict(settings), indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    handle, name = tempfile.mkstemp(dir=target.parent, prefix=f"{target.name}.", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
        os.replace(name, target)
    except BaseException:
        Path(name).unlink(missing_ok=True)
        raise
    return target


def clone(settings: Settings) -> Settings:
    """A copy to edit, so a cancelled dialog leaves nothing behind."""
    return copy.deepcopy(settings)
