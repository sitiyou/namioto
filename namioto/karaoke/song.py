"""Project KRC sound intervals into structured, independently timed engine lyrics."""

from collections.abc import Mapping
from dataclasses import dataclass, replace

from kara_templater import (
    BLACK,
    WHITE,
    Color,
    CueMarker,
    DocumentLyrics,
    DocumentText,
    Lifetime,
    LyricMapping,
    LyricSegment,
    LyricSource,
    LyricUnit,
    RubyText,
    SingingSpan,
    TextAppearance,
    TextRun,
)

from namioto.karaoke.cues import lead_preset
from namioto.karaoke.export_settings import ExportSettings
from namioto.karaoke.sounds import natural_sounds


@dataclass(frozen=True)
class Segment:
    start_ms: float
    end_ms: float
    ruby: str = ""


@dataclass(frozen=True)
class TextUnit:
    text: str
    segments: tuple[Segment, ...] = ()
    ruby_continuation: bool = False

    def __post_init__(self):
        if any(a.end_ms > b.start_ms for a, b in zip(self.segments, self.segments[1:], strict=False)):
            raise ValueError(f"overlapping segments in {self.text!r}")


@dataclass(frozen=True)
class _DisplayLine:
    units: tuple[TextUnit, ...]
    colours: tuple[int, ...]
    segment_colours: tuple[tuple[int, ...], ...]
    first: float
    last: float


def _convert(line, spans, settings, index):
    if len(spans) != len(line.sounds):
        return None
    units, colours, segment_colours, channels = [], [], [], []
    first, last = None, 0
    explicit = []
    for container_index, container in enumerate(line.containers):
        owned = [sound for sound in line.sounds if sound.container == container_index]
        cursor = 0
        for sound in owned:
            if container.key[0] == "top":
                for atom in range(cursor, sound.first_atom):
                    units.append(TextUnit(container.atoms[atom]))
                    channels.append(container.channels[atom])
                    colour = container.color_channels[atom]
                    colours.append(container.channels[atom] if colour is None else colour)
                    segment_colours.append(())
            span = spans[sound.index]
            segment = None
            if span is not None and span[0] is not None and span[1] is not None:
                start, end = (float(value) * 1000 + settings.offset_ms for value in span)
                if end < start:
                    raise ValueError(f"lyric line {index + 1}: reversed interval {start}..{end}")
                if end > 0 or settings.offset_ms >= 0:
                    segment = Segment(max(0, start), max(0, end), sound.reading if sound.rubied else "")
                    explicit.append(segment)
                    if segment.end_ms > segment.start_ms:
                        first = segment.start_ms if first is None else min(first, segment.start_ms)
                        last = max(last, segment.end_ms)
            if sound.rubied and segment is None:
                anchor = next(
                    (
                        max(0, timing[0] * 1000 + settings.offset_ms)
                        for timing in spans[sound.index :]
                        if timing and timing[0] is not None
                    ),
                    0,
                )
                if sound is not owned[0] and units[-1].segments:
                    anchor = units[-1].segments[-1].end_ms
                segment = Segment(anchor, anchor, sound.reading)
            colour = sound.channel if sound.color_channel is None else sound.color_channel
            if sound.rubied and sound is not owned[0]:
                unit = units[-1]
                segments = unit.segments + ((segment,) if segment is not None else ())
                try:
                    units[-1] = replace(unit, segments=segments)
                except ValueError as error:
                    raise ValueError(f"lyric line {index + 1}: {error}") from error
                segment_colours[-1] += (colour,)
            else:
                units.append(
                    TextUnit(
                        sound.base if sound.rubied else sound.reading,
                        (segment,) if segment else (),
                        ruby_continuation=container.key[0] == "part" and container.key[2] > 0,
                    )
                )
                channels.append(sound.channel)
                colours.append(colour)
                segment_colours.append((colour,) if segment else ())
            cursor = sound.last_atom + 1
        if container.key[0] == "top":
            for atom in range(cursor, len(container.atoms)):
                units.append(TextUnit(container.atoms[atom]))
                channels.append(container.channels[atom])
                colour = container.color_channels[atom]
                colours.append(container.channels[atom] if colour is None else colour)
                segment_colours.append(())
    if first is None:
        timed = [
            segment
            for unit in units
            for segment in unit.segments
            if segment is not None and segment.end_ms > segment.start_ms
        ]
        if not timed and not explicit:
            return None
        if timed:
            first = min(segment.start_ms for segment in timed)
            last = max(segment.end_ms for segment in timed)
        else:
            first = min(segment.start_ms for segment in explicit)
            last = max(segment.end_ms for segment in explicit)
    preceding = []
    previous = None
    for unit, colour in zip(units, colours, strict=True):
        preceding.append(previous)
        if unit.segments:
            previous = (colour, unit.segments[-1].end_ms)
    following = None
    for position in range(len(units) - 1, -1, -1):
        unit, colour = units[position], colours[position]
        if unit.segments:
            following = (colour, unit.segments[0].start_ms)
            continue
        previous = preceding[position]
        if following is not None and (previous is None or (previous[0] != colour and colour == following[0])):
            anchor = following[1]
        else:
            anchor = previous[1] if previous is not None else first
        units[position] = replace(unit, segments=(Segment(anchor, anchor),))
        segment_colours[position] = (colour,)
    entries = [
        (i, j, channel, segment)
        for i, (unit, channel) in enumerate(zip(units, channels, strict=True))
        for j, segment in enumerate(unit.segments)
    ]
    durations = [segment.end_ms - segment.start_ms for _, _, _, segment in entries]
    for position, (i, _, channel, segment) in enumerate(entries):
        if durations[position] or not units[i].text.strip() or (settings.offset_ms < 0 and segment.end_ms == 0):
            continue
        for direction in (-1, 1):
            for donor in range(position + direction, len(entries) if direction == 1 else -1, direction):
                if entries[donor][2] != channel:
                    break
                if durations[donor] >= 160:
                    durations[donor] -= 80
                    durations[position] = 80
                    break
            if durations[position]:
                break
        if not durations[position]:
            durations[position] = 10
    adjusted = [list(unit.segments) for unit in units]
    shift, previous_channel = 0, None
    for (i, j, channel, segment), duration in zip(entries, durations, strict=True):
        if channel != previous_channel:
            shift = 0
        previous_channel = channel
        start = max(0, segment.start_ms + shift)
        adjusted[i][j] = replace(segment, start_ms=start, end_ms=start + duration)
        shift += duration - (segment.end_ms - segment.start_ms)
    units = [replace(unit, segments=tuple(segments)) for unit, segments in zip(units, adjusted, strict=True)]
    return _DisplayLine(tuple(units), tuple(colours), tuple(segment_colours), first, last)


@dataclass(frozen=True)
class Song:
    sources: tuple[LyricSource, ...]
    bpm: float
    settings: ExportSettings
    # Colours of individual readings, including repeated singing of the same surface.
    segment_colors: Mapping[str, tuple[tuple[Color, ...], ...]]


def prepare_song(text, spans, settings: ExportSettings, bpm: float = 120) -> Song:
    sources = []
    chapters = set()
    palettes = {}
    palette = settings.channel_colours()
    roles = settings.vocal_roles()
    lead, _ = lead_preset(settings).resolve_timing(bpm=bpm)

    def color(channel):
        return Color.hex("#" + palette.get(channel, settings.overlay_color).lstrip("#"))

    for index, line in enumerate(natural_sounds(text)):
        row = _convert(line, spans[index] if index < len(spans) else (), settings, index)
        if row is None:
            continue
        family = roles.get(line.track, "default")
        first = line.chapter not in chapters and family == "default"
        if first:
            chapters.add(line.chapter)
        identity = f"line-{index + 1}"
        runs, units = [], []
        offset = 0
        for unit, channel, channels in zip(row.units, row.colours, row.segment_colours, strict=True):
            end = offset + len(unit.text)
            appearance = TextAppearance(family, color(channel), WHITE, BLACK, BLACK, frozenset(("primary",)))
            runs.append(TextRun(unit.text, offset, end, appearance))
            segments = []
            for segment, segment_channel in zip(unit.segments, channels, strict=True):
                reading = None
                if segment.ruby and settings.show_ruby:
                    length = len(segment.ruby)
                    run = TextRun(
                        segment.ruby,
                        0,
                        length,
                        replace(
                            appearance,
                            primary=Color.hex("#" + settings.ruby_color.lstrip("#"))
                            if settings.ruby_color
                            else color(segment_channel),
                        ),
                    )
                    reading = RubyText(
                        DocumentText(None, (), (run,), (), (), ()),
                        (LyricMapping(0, length, 0, length),),
                        "join" if unit.ruby_continuation or segments else "spill",
                    )
                clock = SingingSpan(offset, end, segment.start_ms / 1000, segment.end_ms / 1000, "k")
                segments.append(LyricSegment(clock, offset, end, reading))
            units.append(LyricUnit(offset, end, tuple(segments)))
            offset = end
        cues = ()
        if first and settings.cue != "off" and row.first / 1000 >= lead:
            placement = "inline" if settings.cue_placement == "inline" else "independent"
            cues = (CueMarker("lead", "lead", placement=placement),)
        body = DocumentText(None, (), tuple(runs), (), cues, ())
        lyrics = DocumentLyrics(body, body, (LyricMapping(0, offset, 0, offset),), tuple(units))
        sources.append(
            LyricSource.from_lyrics(
                identity,
                lyrics,
                style=family,
                actor=str(line.chapter),
                display=Lifetime(row.first / 1000, max(row.first + 1, row.last) / 1000),
            )
        )
        palettes[identity] = tuple(tuple(color(c) for c in channels) for channels in row.segment_colours)
    return Song(tuple(sources), bpm, settings, palettes)
