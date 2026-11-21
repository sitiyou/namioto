"""Chapter entrance preset shared by lyric preparation and the default template."""

from dataclasses import replace

from kara_templater import (
    AnimationClip,
    BarsCue,
    Color,
    CueContent,
    CuePreset,
    FadeIn,
    FadeOut,
    PathObject,
    RectangleClip,
    RectangleKeyframes,
    Vec2,
    VectorPath,
    VisualState,
    VolumeCue,
)

from namioto.karaoke.export_settings import ExportSettings


def _dot_countdown(settings, sung):
    """Three outlined lamps fill on successive beats and leave together."""

    def draw(context):
        size = _guide_size(context, settings)
        lamps = []
        radius = size / 2
        diameter = size + 2 * settings.border
        pitch = diameter + size * 0.3
        path = VectorPath.circle(radius)
        reach = radius + settings.border + 1
        anchor = context.lifetime.start
        length = context.lifetime.end - anchor
        # Inline lamps inherit the host row's entrance fade.
        fade_in = 0 if settings.cue_placement == "inline" else min(settings.fade_in_ms / 1000, length / 2)
        fade_out = min(settings.fade_out_ms / 1000, length - fade_in)
        fades = tuple(
            clip
            for clip in (
                AnimationClip(0, fade_in, FadeIn()) if fade_in else None,
                AnimationClip(length - fade_out, fade_out, FadeOut()) if fade_out else None,
            )
            if clip is not None
        )
        for i, (start, end) in enumerate(zip(context.steps[:-1], context.steps[1:], strict=True)):
            slot = i if settings.cue_direction == "left_to_right" else len(context.steps) - 2 - i
            state = VisualState(
                position=Vec2(settings.border + radius + slot * pitch, settings.border + radius),
                fill=Color.hex("#" + settings.base_color.lstrip("#")),
                border=settings.border,
                outline=Color.hex("#" + settings.base_outline_color.lstrip("#")),
            )
            base = PathObject(
                id=f"{context.id}/dot-{i}", path=path, state=state, lifetime=context.lifetime, animations=fades
            )
            keys = RectangleKeyframes(
                ((start - anchor, (-reach, -reach, -reach, reach)), (end - anchor, (-reach, -reach, reach, reach)))
            )
            lit = replace(
                base,
                id=base.id + "/sung",
                layer=1,
                state=replace(state, fill=sung, outline=Color.hex("#" + settings.overlay_outline_color.lstrip("#"))),
                clip=RectangleClip(-reach, -reach, reach, reach, space="local", deform=keys),
            )
            lamps.extend((base, lit))
        return CueContent(
            tuple(lamps), diameter + (len(context.steps) - 2) * pitch, diameter
        )

    return draw


def _guide_size(context, settings):
    font_size = context.host.font_size if context.host else settings.font_size
    return max(4, int(font_size * settings.cue_size_ratio + 0.5))


def lead_preset(settings: ExportSettings) -> CuePreset:
    color = Color.hex("#" + (settings.cue_color or settings.overlay_color).lstrip("#"))
    def bars(context):
        size = _guide_size(context, settings)
        return BarsCue(width=size * 2, height=size, color=color, direction=settings.cue_direction)(context)

    factory = bars if settings.cue == "bars" else _dot_countdown(settings, color)
    preset = CuePreset(factory=factory, gap=None if settings.cue_gap == -1 else settings.cue_gap)
    if settings.cue == "volume":
        preset = replace(preset, factory=VolumeCue(size_ratio=settings.cue_size_ratio,
                                                   sung=color if settings.cue_color else None),
                         steps=(0, 0.25, 0.5, 0.75, 1))
    if settings.guide_dot_duration_ms:
        duration = settings.guide_dot_duration_ms * (len(preset.steps) - 1) / 1000
        return replace(preset, duration=duration)
    return preset
