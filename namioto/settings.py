# SPDX-License-Identifier: AGPL-3.0-only
"""What the program remembers between runs: the spec table of every value, its file and the defaults.

Qt-free on purpose. The spec table below is the single source of truth for the program: it gives the
defaults, tells `load` how to read a value back from the file, and lets `namioto.ui.settings_dialog`
build its pages without repeating any of it. `namioto.params` holds the `Field` primitive the table
is written with, and the align and transcription windows describe their own parameter files with it.

A field a bar control already sets is `hidden` and gets no row, and so is a project field with no
control; `scope="project"` marks a document field, read and written by `project_values` /
`apply_project_values` on this same table. Precedence is project file > settings file > built-in
default, with the command line on top for one run, and `remembered=False` marks the two song-scoped
values (tempo, the grid offset) whose default is written back rather than what the user left.
"""

from __future__ import annotations

import copy
import json
import os
import warnings
from pathlib import Path
from typing import Any

from namioto import params
from namioto.analysis import devices
from namioto.analysis.choices import ALGORITHMS, CHANNEL_MODES
from namioto.i18n import LANGUAGE_CODES, LANGUAGE_LABELS, SYSTEM
from namioto.params import Field, Section
from namioto.utils import config_dir, write_json

VERSION = 5
DIVISIONS = ("beats", "seconds")

SECTIONS: tuple[Section, ...] = (
    Section(
        "general",
        "General",
        "General",
        (
            Field(
                "language",
                "choice",
                SYSTEM,
                "Language",
                "Which language the interface speaks; a change takes effect the next time it starts",
                choices=LANGUAGE_CODES,
                labels=LANGUAGE_LABELS,
            ),
            Field(
                "auto_save",
                "bool",
                False,
                "Auto-save",
                "Save the open project once editing stops, and when the window loses focus",
            ),
            Field(
                "style",
                "style",
                "",
                "Style",
                "Which widget style draws the window; the desktop's own unless another one is picked",
            ),
        ),
    ),
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
                hidden=True,
                scope="project",
                choices=CHANNEL_MODES,
            ),
            Field(
                "t_num",
                "float",
                40.0,
                "Frames/s",
                "Analysis frames per second: the time resolution of the spectrum",
                hidden=True,
                scope="project",
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
                hidden=True,
                scope="project",
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
                hidden=True,
                scope="project",
                low=400,
                high=480,
                step=0.5,
                decimals=1,
            ),
        ),
    ),
    Section(
        "hardware",
        "Devices",
        "Device",
        (
            Field(
                "gpu",
                "device",
                devices.AUTO,
                "GPU",
                "Which GPU backend a run that asks for the GPU uses; the line under it says whether "
                "its runtime is installed",
                choices=(devices.AUTO, *devices.GPU_KEYS),
                labels=("Automatic", *devices.GPU_LABELS),
            ),
            Field(
                "power",
                "choice",
                devices.POWER_PREFERENCES[0],
                "GPU power",
                "Which of the machine's GPUs the WebGPU backend runs on: the discrete one, or the "
                "integrated one to save power",
                choices=devices.POWER_PREFERENCES,
                labels=devices.POWER_LABELS,
            ),
        ),
    ),
    Section(
        "spectrum",
        "Display",
        "Spectrum",
        (
            Field(
                "gain",
                "float",
                240.0,
                "Gain",
                "Energy it takes for the spectrum to reach full red",
                hidden=True,
                low=10,
                high=600,
                step=1,
                scope="project",
            ),
            Field(
                "contrast",
                "float",
                1.0,
                "Contrast",
                "Exponent applied to the spectrum's energy",
                hidden=True,
                scope="project",
                low=0.2,
                high=4.0,
                step=0.1,
                decimals=1,
            ),
        ),
    ),
    Section(
        "playback",
        "Playback",
        "Playback",
        (
            Field(
                "audio_volume",
                "int",
                80,
                "Audio volume",
                "Starting volume of the analysed audio",
                hidden=True,
                scope="project",
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
                hidden=True,
                scope="project",
                low=0,
                high=100,
                suffix="%",
            ),
            Field(
                "latency_ms",
                "int",
                0,
                "Grid offset (ms)",
                "Shifts the drawn grid lines by this many ms; - left, + right, playback untouched",
                hidden=True,
                remembered=False,
                scope="project",
                low=-500,
                high=500,
            ),
            Field(
                "speed",
                "float",
                1.0,
                "Speed",
                "Playback speed in 5% steps, 0.10x to 2.00x; the pitch is left alone",
                hidden=True,
                scope="project",
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
                "snap",
                "float",
                0.5,
                "Snap",
                "Snap grid for the pen tool",
                hidden=True,
                low=0.0625,
                high=4.0,
                scope="project",
            ),
            Field(
                "division",
                "choice",
                "beats",
                "Division",
                "What the ruler's lower row and the drawn grid lines divide by",
                hidden=True,
                scope="project",
                choices=DIVISIONS,
            ),
            Field(
                "zoom_x",
                "float",
                48.0,
                "Zoom x",
                "Pixels per beat at startup",
                hidden=True,
                scope="project",
                low=12,
                high=900,
                decimals=1,
            ),
            Field(
                "zoom_y",
                "float",
                16.0,
                "Zoom y",
                "Pixels per semitone row at startup",
                hidden=True,
                low=8,
                high=64,
                decimals=1,
                scope="project",
            ),
            Field(
                "auto_page",
                "bool",
                False,
                "Auto page turn",
                "Take the next page of the roll once the playhead reaches the right of the window",
                hidden=True,
            ),
            Field(
                "overtone_highlight",
                "bool",
                False,
                "Overtone highlight",
                "Paint the overtones of the row under the mouse - f, 2f, 3f and 4f - as well",
                hidden=True,
            ),
        ),
    ),
    Section(
        "tempo",
        "Tempo",
        "Tempo",
        (
            Field(
                "bpm",
                "float",
                120.0,
                "Tempo",
                "Tempo of the beat grid at startup",
                hidden=True,
                remembered=False,
                scope="project",
                low=20,
                high=300,
                step=0.1,
                decimals=1,
            ),
            Field(
                "estimator",
                "choice",
                ALGORITHMS[0],
                "Algorithm",
                "Which algorithm estimates the tempo from the audio",
                choices=ALGORITHMS,
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
        "lyrics",
        "Lyrics",
        "Lyrics",
        (
            Field(
                "api_base",
                "text",
                "",
                "API base",
                "OpenAI-compatible endpoint up to its /v1, such as https://api.deepseek.com/v1",
            ),
            Field(
                "api_key",
                "secret",
                "",
                "API key",
                "Bearer token sent to that endpoint; the settings file keeps it in plain text",
            ),
            Field(
                "model",
                "text",
                "",
                "Model",
                "Model name the endpoint serves, such as deepseek-chat",
            ),
            Field(
                "temperature",
                "float",
                0.2,
                "Temperature",
                "How far the model may wander; the annotation wants it low",
                low=0.0,
                high=2.0,
                step=0.1,
                decimals=1,
                advanced=True,
            ),
            Field(
                "timeout",
                "float",
                120.0,
                "Timeout (s)",
                "How long one request may take before it is given up on",
                low=1.0,
                high=600.0,
                step=1.0,
                advanced=True,
            ),
            Field(
                "editor",
                "text",
                "",
                "External editor",
                "Command that opens a .krc, such as code; empty picks the platform's own",
            ),
            Field(
                "auto_align",
                "bool",
                True,
                "Auto-align",
                "Re-align the lyrics when the .krc changes and the model's cached pass over the audio is there",
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
                "text",
                "",
                "Last directory",
                "Where the file chooser starts",
                hidden=True,
            ),
        ),
    ),
    Section(
        "network",
        "Advanced",
        "Network",
        (
            Field(
                "github_mirror",
                "text",
                "https://gh-proxy.org",
                "GitHub mirror",
                "Downloads of GitHub releases are fetched through this mirror; empty goes straight to github.com",
            ),
            Field(
                "proxy",
                "text",
                "",
                "Proxy",
                "HTTP(S) proxy for downloads and the lyrics API, such as http://127.0.0.1:7890; "
                "empty follows the environment",
            ),
        ),
    ),
    Section(
        "session",
        "Advanced",
        "Session",
        (
            Field("geometry", "text", "", "Window geometry", hidden=True),
            Field(
                "center_x",
                "float",
                8.0,
                "View center x",
                hidden=True,
                low=0.0,
                high=10000.0,
                decimals=1,
                scope="project",
            ),
            Field(
                "center_y",
                "float",
                48.0,
                "View center y",
                hidden=True,
                low=0.0,
                high=88.0,
                decimals=1,
                scope="project",
            ),
        ),
    ),
    Section(
        "midi",
        "Advanced",
        "MIDI",
        (
            Field(
                "wavetone",
                "bool",
                True,
                "WaveTone compatibility",
                "WaveTone's MIDI export starts every note one bar late: reading that back turns it "
                "back, and writing MIDI adds it, so files and the two programs agree",
            ),
        ),
    ),
)


Settings = params.build_types(VERSION, SECTIONS)
FIELD_SPECS = {(section.name, item.name): item for section in SECTIONS for item in section.fields}
PROJECT_FIELDS = tuple(
    (section.name, item) for section in SECTIONS for item in section.fields if item.scope == "project"
)


def project_values(settings: Settings) -> dict[str, dict]:
    """The part of the settings that belongs to a document rather than to the machine."""
    values: dict[str, dict] = {}
    for section, item in PROJECT_FIELDS:
        values.setdefault(section, {})[item.name] = get_value(settings, section, item.name)
    return values


def apply_project_values(settings: Settings, data: Any) -> None:
    """Put a project's values on a settings object, ignoring the keys that are not project fields."""
    if not isinstance(data, dict):
        return
    for section, item in PROJECT_FIELDS:
        stored = data.get(section)
        if isinstance(stored, dict) and item.name in stored:
            set_value(settings, section, item.name, stored[item.name])


def default_path() -> Path:
    """`$NAMIOTO_SETTINGS`, else the platform's config directory, where preferences belong."""
    from_env = os.environ.get("NAMIOTO_SETTINGS")
    if from_env:
        return Path(from_env)
    return config_dir("settings.json")


def get_value(settings: Settings, section: str, name: str) -> Any:
    return getattr(getattr(settings, section), name)


def set_value(settings: Settings, section: str, name: str, value: Any) -> None:
    """Put `value` on one field, first making it fit the field's type and range."""
    setattr(getattr(settings, section), name, params.coerce(FIELD_SPECS[(section, name)], value))


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
                    setattr(getattr(settings, section.name), item.name, params.coerce(item, stored[item.name]))
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
    return write_json(to_dict(settings), Path(path) if path is not None else default_path())


def clone(settings: Settings) -> Settings:
    """A copy to edit, so a cancelled dialog leaves nothing behind."""
    return copy.deepcopy(settings)
