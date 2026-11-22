"""Namioto's default singing wipe, row allocation and tempo count-in."""

from dataclasses import dataclass, replace
from itertools import groupby

from kara_templater import (
    Box,
    Color,
    Document,
    FadeIn,
    FadeOut,
    Glow,
    HighlightArrival,
    Layout,
    Lifetime,
    LyricHighlights,
    LyricRule,
    Placement,
    ScalePulse,
    StatePatch,
    Style,
    TimeSpan,
    Track,
    Vec2,
    prepare_tracks,
)

from namioto.karaoke.cues import lead_preset
from namioto.karaoke.song import Song


def _body_interval(_, target):
    if not target.singing:
        return ()
    return (TimeSpan(target.singing[0].start + target.delay, target.singing[-1].end + target.delay),)


def _chapter_rows(items, chapter, rows):
    lanes = [[] for _ in range(rows)]
    for _, section in groupby(items, key=chapter):
        for index, item in enumerate(section):
            lanes[index % rows].append(item)
    return lanes


@dataclass(frozen=True, kw_only=True)
class VocalRows(Layout):
    top: bool = False

    def allocate(self, lines, context):
        # The inline count-in and its host enter as one row. The reserved
        # interval already includes the countdown computed by the cue preset.
        lines = tuple(
            replace(line, display=Lifetime(line.required.start, line.display.end))
            if any(cue.placement == "inline" for cue in line.cues)
            else line
            for line in lines
        )
        layout = self
        if self.gap is not None:
            height = max((line.bounds.height for line in lines), default=0)
            layout = replace(self, gap=max(self.gap, height))
        if self.alignment == "alternating":
            # Keep the two fixed horizontal slots through every page handoff.
            bottom = context.region.top + context.region.height
            result = []
            placements = super().allocate(lines, context)
            offsets = layout.row_offsets(lines, placements, from_bottom=True)
            for line, placement in zip(lines, placements, strict=True):
                y = bottom - line.bounds.height - offsets[line.id]
                if y < context.region.top:
                    raise ValueError(f"subtitle row {line.id} does not fit vertically")
                position = Vec2(placement.position.x, y - line.bounds.top)
                result.append(replace(placement, position=position))
            return tuple(result)
        lanes = []
        chapter = None
        counter = 0
        for line in lines:
            if line.tags != chapter:
                chapter, counter = line.tags, 0
            row = counter % self.rows
            counter += 1
            candidates = [row, *(i for i in range(len(lanes)) if i != row)]
            row = next(
                (i for i in candidates if i >= len(lanes) or lanes[i][-1].required.end <= line.required.start),
                len(lanes),
            )
            if row == len(lanes):
                lanes.append([])
            lanes[row].append(line)
        result = []
        area = context.region
        for row, lane in enumerate(lanes):
            for i, line in enumerate(lane):
                start = max(line.display.start, lane[i - 1].required.end if i else 0)
                end = min(line.display.end, lane[i + 1].required.start if i + 1 < len(lane) else line.display.end)
                if i:
                    start = max(start, min(lane[i - 1].display.end, line.required.start))
                y = area.top if self.top else area.top + area.height - line.bounds.height
                align = self.alignment[row % self.rows] if isinstance(self.alignment, tuple) else self.alignment
                fraction = {"left": 0, "center": 0.5, "right": 1}[align]
                x = area.left + (area.width - line.bounds.width) * fraction
                pos = Vec2(x - line.bounds.left, y - line.bounds.top)
                result.append(Placement(line.id, pos, Lifetime(start, end), row))
        offsets = layout.row_offsets(lines, tuple(result), from_bottom=not self.top)
        bounds_by_id = {line.id: line.bounds for line in lines}
        for i, placement in enumerate(result):
            offset = offsets[placement.id] if self.top else -offsets[placement.id]
            position = placement.position + Vec2(0, offset)
            bounds = bounds_by_id[placement.id]
            y = position.y + bounds.top
            if y < area.top or y + bounds.height > area.top + area.height:
                raise ValueError(f"subtitle row {placement.id} does not fit vertically")
            result[i] = replace(placement, position=position)
        return tuple(result)


def default_scene(song: Song):
    settings = song.settings

    def rgb(value):
        return Color.hex("#" + value.lstrip("#"))

    styles = []
    for family, ratio in (("default", 1), ("top", settings.harmony_ratio)):
        style = Style(
            name=family,
            fontname=settings.font,
            fontsize=settings.font_size * ratio,
            spacing=settings.spacing,
            outline=settings.border,
            shadow=0,
        )
        styles.extend(
            (
                style,
                replace(
                    style,
                    name=family + "-furigana",
                    fontname=settings.ruby_font or settings.font,
                    fontsize=style.fontsize * settings.ruby_ratio,
                    outline=settings.border_furi,
                ),
            )
        )
    document = Document.create(width=1920, height=1080, styles=tuple(styles))
    preset = lead_preset(settings)
    lead, duration = preset.resolve_timing(bpm=song.bpm)
    base = StatePatch(fill=rgb(settings.base_color), outline=rgb(settings.base_outline_color))
    sung = StatePatch(outline=rgb(settings.overlay_outline_color))
    sources = {s.id: s for s in song.sources}

    # Fades reserve time in kara-templater. Budget them inside the actual
    # same-side gap before handing the track to its strict row allocator.
    fades = {}
    if settings.layout == "double":
        windows = []
        for source in song.sources:
            if source.style == "default":
                spans = [segment.singing for unit in source.lyrics.units for segment in unit.segments]
                windows.append((source.id, min(span.start for span in spans), max(span.end for span in spans)))
        windows.sort(key=lambda item: item[1])
        fades = {
            identity: [min(settings.fade_in_ms / 1000, start), settings.fade_out_ms / 1000]
            for identity, start, _ in windows
        }
        for lane in _chapter_rows(windows, lambda item: sources[item[0]].actor, 2):
            for (previous, _, end), (following, start, _) in zip(lane, lane[1:], strict=False):
                cue_lead = lead if sources[following].lyrics.body.cues else 0
                room = max(0, start - cue_lead - end)
                fades[previous][1] = min(fades[previous][1], room)
                fades[following][0] = min(fades[following][0], max(0, start - (end + fades[previous][1])))

    def decorate(line):
        source = sources[line.id.split("/", 1)[1]]
        colors = song.segment_colors[source.id]
        units = source.lyrics.units

        by_span = {
            seg.singing: color
            for unit, palette in zip(units, colors, strict=True)
            for seg, color in zip(unit.segments, palette, strict=True)
        }

        def glow(target, sung_side):
            if not settings.blur and not settings.blur_scale:
                return None
            span = target.singing[0] if target.singing else None
            color = source.lyrics.body.runs[0].appearance.primary
            if span:
                color = by_span.get(span, color)
            code = f"{color.red:02X}{color.green:02X}{color.blue:02X}"
            after, before = replace(settings, overlay_color=code).blur_colours()
            border = settings.border_furi if target.role == "ruby" else settings.border
            return Glow(
                color=rgb(after if sung_side else before),
                blur=settings.blur,
                border=border * settings.blur_scale,
                opacity=0.8,
            )

        segment_colors = {
            (i, j): color for i, palette in enumerate(colors) if len(palette) > 1 for j, color in enumerate(palette)
        }
        effect = LyricHighlights.from_singing(
            sung,
            base_paint=base,
            hook_feather=settings.feather,
            segment_colors=segment_colors,
            ruby_fill=rgb(settings.ruby_color) if settings.ruby_color else None,
            blend_glow=False,
            ass_feather_bands=6,
            glow=lambda _, target: glow(target, True),
            base_glow=lambda _, target: glow(target, False),
        )
        line = effect(line)
        entrance, exit_duration = fades.get(
            source.id, (min(settings.fade_in_ms / 1000, source.display.start), settings.fade_out_ms / 1000)
        )
        if any(cue.placement == "inline" for cue in source.lyrics.body.cues):
            entrance = min(entrance, duration)
        rules = []
        if entrance:
            rules.append(LyricRule("line", "enter", FadeIn(), duration=entrance))
        if exit_duration:
            rules.append(LyricRule("line", "exit", FadeOut(), duration=exit_duration))
        if settings.pulse != 1:
            pulse = ScalePulse(peak=settings.pulse, peak_at=settings.pulse_peak)
            rules.append(LyricRule("character", _body_interval, pulse))
            rules.append(LyricRule("ruby", HighlightArrival(highlights=effect.highlights), pulse))
        return line.schedule(*rules)

    area = Box(settings.margin_h, settings.margin_v, 1920 - 2 * settings.margin_h, 1080 - 2 * settings.margin_v)
    tracks = []
    for family in ("default", "top"):
        top = family == "top"
        rows = 1 if settings.layout == "single" or top else 2
        allocation = VocalRows(
            rows=rows,
            top=top,
            gap=settings.row_gap or None,
            alignment=settings.harmony_align if top else "alternating" if rows == 2 else "center",
        )
        tracks.append(
            Track(
                id=family,
                source=lambda s, family=family: s.style == family,
                tags=lambda s: frozenset((s.actor,)),
                region=area,
                layout=allocation,
                preview=settings.lead_time_ms / 1000,
                tail=(settings.hold_ms + settings.fade_out_ms) / 1000,
                ruby_gap=settings.ruby_offset,
                cues={"lead": replace(preset, line_edge="below" if top else "above")},
                effects=(decorate,),
            )
        )
    if settings.palt:
        if not hasattr(Track, "palt"):
            raise ValueError("Proportional font metrics require a kara-templater build with Track.palt support")
        tracks = [replace(track, palt=True) for track in tracks]
    return prepare_tracks(document, inputs=song.sources, tracks=tuple(tracks), bpm=song.bpm)
