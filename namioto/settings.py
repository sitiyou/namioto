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

VERSION = 2
TEXT_LIMIT = 4096
DIVISIONS = ("beats", "seconds")
# the General MIDI program list, in the order the program change is meant to select them in
GM_PROGRAMS = (
    "Acoustic Grand Piano",
    "Bright Acoustic Piano",
    "Electric Grand Piano",
    "Honky-tonk Piano",
    "Electric Piano 1",
    "Electric Piano 2",
    "Harpsichord",
    "Clavinet",
    "Celesta",
    "Glockenspiel",
    "Music Box",
    "Vibraphone",
    "Marimba",
    "Xylophone",
    "Tubular Bells",
    "Dulcimer",
    "Drawbar Organ",
    "Percussive Organ",
    "Rock Organ",
    "Church Organ",
    "Reed Organ",
    "Accordion",
    "Harmonica",
    "Tango Accordion",
    "Acoustic Guitar (nylon)",
    "Acoustic Guitar (steel)",
    "Electric Guitar (jazz)",
    "Electric Guitar (clean)",
    "Electric Guitar (muted)",
    "Overdriven Guitar",
    "Distortion Guitar",
    "Guitar Harmonics",
    "Acoustic Bass",
    "Electric Bass (finger)",
    "Electric Bass (pick)",
    "Fretless Bass",
    "Slap Bass 1",
    "Slap Bass 2",
    "Synth Bass 1",
    "Synth Bass 2",
    "Violin",
    "Viola",
    "Cello",
    "Contrabass",
    "Tremolo Strings",
    "Pizzicato Strings",
    "Orchestral Harp",
    "Timpani",
    "String Ensemble 1",
    "String Ensemble 2",
    "Synth Strings 1",
    "Synth Strings 2",
    "Choir Aahs",
    "Voice Oohs",
    "Synth Voice",
    "Orchestra Hit",
    "Trumpet",
    "Trombone",
    "Tuba",
    "Muted Trumpet",
    "French Horn",
    "Brass Section",
    "Synth Brass 1",
    "Synth Brass 2",
    "Soprano Sax",
    "Alto Sax",
    "Tenor Sax",
    "Baritone Sax",
    "Oboe",
    "English Horn",
    "Bassoon",
    "Clarinet",
    "Piccolo",
    "Flute",
    "Recorder",
    "Pan Flute",
    "Blown Bottle",
    "Shakuhachi",
    "Whistle",
    "Ocarina",
    "Lead 1 (square)",
    "Lead 2 (sawtooth)",
    "Lead 3 (calliope)",
    "Lead 4 (chiff)",
    "Lead 5 (charang)",
    "Lead 6 (voice)",
    "Lead 7 (fifths)",
    "Lead 8 (bass + lead)",
    "Pad 1 (new age)",
    "Pad 2 (warm)",
    "Pad 3 (polysynth)",
    "Pad 4 (choir)",
    "Pad 5 (bowed)",
    "Pad 6 (metallic)",
    "Pad 7 (halo)",
    "Pad 8 (sweep)",
    "FX 1 (rain)",
    "FX 2 (soundtrack)",
    "FX 3 (crystal)",
    "FX 4 (atmosphere)",
    "FX 5 (brightness)",
    "FX 6 (goblins)",
    "FX 7 (echoes)",
    "FX 8 (sci-fi)",
    "Sitar",
    "Banjo",
    "Shamisen",
    "Koto",
    "Kalimba",
    "Bag pipe",
    "Fiddle",
    "Shanai",
    "Tinkle Bell",
    "Agogo",
    "Steel Drums",
    "Woodblock",
    "Taiko Drum",
    "Melodic Tom",
    "Synth Drum",
    "Reverse Cymbal",
    "Guitar Fret Noise",
    "Breath Noise",
    "Seashore",
    "Bird Tweet",
    "Telephone Ring",
    "Helicopter",
    "Applause",
    "Gunshot",
)
PROGRAM_LABELS = tuple(f"{index}: {name}" for index, name in enumerate(GM_PROGRAMS))


@dataclass(frozen=True)
class Field:
    """One setting: its default, how to read it back, and how the dialog shows it."""

    name: str
    kind: str  # bool, int, float, choice or text
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
                scope="project",
                choices=CHANNEL_MODES,
            ),
            Field(
                "t_num",
                "float",
                40.0,
                "Frames/s",
                "Analysis frames per second: the time resolution of the spectrum",
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
                scope="project",
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
                "Latency (ms)",
                "Offset between the sound and the displayed waveform",
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


def write_json(data: dict, path: str | Path) -> Path:
    """Write a whole file at once, with the layout both settings and projects use."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    handle, name = tempfile.mkstemp(dir=target.parent, prefix=f"{target.name}.", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
        os.replace(name, target)
    except BaseException:
        Path(name).unlink(missing_ok=True)
        raise
    return target


def save(settings: Settings, path: str | Path | None = None) -> Path:
    """Write the settings out whole: a half-written file would be read as a broken one."""
    return write_json(to_dict(settings), Path(path) if path is not None else default_path())


def clone(settings: Settings) -> Settings:
    """A copy to edit, so a cancelled dialog leaves nothing behind."""
    return copy.deepcopy(settings)
