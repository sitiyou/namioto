"""Standard ASS convenience entry point for already aligned sounds."""

from namioto.karaoke.export_settings import ExportSettings


def generate_ass(lyrics_text, spans, *, settings: ExportSettings | None = None) -> str:
    from namioto.karaoke.default_template import default_scene
    from namioto.karaoke.output import encode_ass
    from namioto.karaoke.song import Song, prepare_song

    song = prepare_song(lyrics_text, spans, settings or ExportSettings())
    assert isinstance(song, Song)
    return encode_ass(default_scene(song, for_ass=True), quantize_clocks=True)
