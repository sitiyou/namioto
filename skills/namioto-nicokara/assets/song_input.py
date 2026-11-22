"""Song-local timing handoff. Copy beside the matched KRC and MIDI."""

from pathlib import Path

from namioto.karaoke.export_settings import ExportSettings
from namioto.subtitle import build_song

ROOT = Path(__file__).resolve().parent
WAVETONE = False
OFFSET_MS = -100
TRACK_STYLE = {}
CHANNEL_COLORS = {}


def load_song():
    return build_song(
        (ROOT / "lyrics.krc").read_text(encoding="utf-8"),
        (ROOT / "vocal.mid").read_bytes(),
        wavetone=WAVETONE,
        semantic=True,
        settings=ExportSettings(offset_ms=OFFSET_MS, track_style=TRACK_STYLE, channel_colors=CHANNEL_COLORS),
    )
