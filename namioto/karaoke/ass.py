"""Write plain ASS dialogue timed by the supplied lyric mapping."""

from collections.abc import Sequence

from namioto.karaoke.export_settings import ExportSettings, validate_settings
from namioto.karaoke.sounds import SoundLine, natural_sounds

_HEADER = (
    "[Script Info]\n"
    "ScriptType: v4.00+\n"
    "PlayResX: 1920\n"
    "PlayResY: 1080\n\n"
    "[V4+ Styles]\n"
    "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
    "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
    "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
    "Style: Default,Arial,54,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,2,40,40,48,1\n\n"
    "[Events]\n"
    "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
)


def _timestamp(ticks: int) -> str:
    seconds, fraction = divmod(ticks, 100)
    minutes, second = divmod(seconds, 60)
    hours, minute = divmod(minutes, 60)
    return f"{hours}:{minute:02d}:{second:02d}.{fraction:02d}"


def write_ass(
    lines: Sequence[SoundLine],
    spans: Sequence[Sequence[tuple[float, float] | None]],
    settings: ExportSettings,
) -> str:
    validate_settings(settings)
    offset = settings.offset_ms / 1000
    events = []
    for line, row in zip(lines, spans, strict=True):
        if len(row) != len(line.sounds):
            raise ValueError("lyric spans must match the sounds in each line")
        if any(span is not None and span[1] < span[0] for span in row):
            raise ValueError("a lyric sound has a reversed interval")
        timed = [span for span in row if span is not None and span[0] < span[1]]
        if not timed:
            continue
        end = max(span[1] for span in timed) + offset
        if end <= 0:
            continue
        start = max(0, min(span[0] for span in timed) + offset)
        first = round(start * 100)
        last = max(first + 1, round(end * 100))
        # Interrupt literal ASS escapes and protect braces from override parsing.
        text = line.text.replace("\\", "\\\u2060").replace("{", r"\{").replace("}", r"\}")
        events.append(f"Dialogue: 0,{_timestamp(first)},{_timestamp(last)},Default,,0,0,0,,{text}\n")
    return _HEADER + "".join(events)


def generate_ass(lyrics_text, spans, *, settings: ExportSettings | None = None) -> str:
    return write_ass(natural_sounds(lyrics_text), spans, settings or ExportSettings())
