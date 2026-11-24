"""Native ASS encoding for complete songs."""

from dataclasses import replace

from kara_templater import (
    AnimationClip,
    AssBudget,
    AssDialogue,
    Lifetime,
    ObjectGroup,
    RectangleClip,
    RectangleKeyframes,
    Scene,
    SceneObject,
)

SONG_BUDGET = AssBudget(max_events=1_000_000, max_bytes=64 * 1024 * 1024)


def _ass_clocks(node: SceneObject) -> SceneObject:
    """Align default-template fades and wipe knots to the same ASS clock."""
    start = round(node.lifetime.start * 100)
    end = max(start + 1, round(node.lifetime.end * 100))
    origin = round(node.time_origin * 100)
    lifetime = Lifetime(start / 100, end / 100)
    clock = origin / 100

    def local_tick(time: float) -> int:
        return round((node.time_origin + time) * 100) - origin

    def local_seconds(tick: int) -> float:
        return min(lifetime.end - clock, max(-clock, tick / 100))

    animations: list[AnimationClip] = []
    for animation in node.animations:
        first, last = local_tick(animation.start), local_tick(animation.end)
        if first < last:
            animations.append(replace(animation, start=first / 100, duration=(last - first) / 100))

    clip = node.clip
    if isinstance(clip, RectangleClip) and isinstance(clip.deform, RectangleKeyframes):
        frames: dict[int, list[tuple[float, float, float, float]]] = {}
        for time, bounds in clip.deform.keyframes:
            frames.setdefault(local_tick(time), []).append(bounds)
        keys = tuple(
            (local_seconds(tick), bounds)
            for tick, limits in frames.items()
            for bounds in ((limits[0], limits[-1]) if limits[0] != limits[-1] else (limits[0],))
        )
        clip = replace(clip, deform=RectangleKeyframes(keys))

    snapped = replace(node, lifetime=lifetime, clock_start=clock, animations=tuple(animations), clip=clip)
    if isinstance(snapped, ObjectGroup):
        return replace(snapped, children=tuple(_ass_clocks(child) for child in snapped.children))
    return snapped


def encode_ass(scene: Scene, *, quantize_clocks: bool = False) -> str:
    if quantize_clocks:
        scene = replace(
            scene,
            objects=[node if isinstance(node, AssDialogue) else _ass_clocks(node) for node in scene.objects],
        )
    return scene.ass.compile(budget=SONG_BUDGET, native_tweens=True).document.dumps()
