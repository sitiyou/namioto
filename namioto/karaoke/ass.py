"""Standard ASS convenience entry point for already aligned sounds."""

from namioto.karaoke.default_template import default_scene
from namioto.karaoke.export_settings import ExportSettings
from namioto.karaoke.output import encode_ass
from namioto.karaoke.song import prepare_song


def generate_ass(lyrics_text, spans, *, settings: ExportSettings | None = None) -> str:
    song = prepare_song(lyrics_text, spans, settings or ExportSettings())
    return encode_ass(default_scene(song))
