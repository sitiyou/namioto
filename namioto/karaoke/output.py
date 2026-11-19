"""Standard ASS encoding for complete songs, including sampled Python motion."""

from kara_templater import Scene, SceneBudget

# Preflight counts samples before adjacent identical drawings are merged.
# Keep independent limits on final file size and concurrent drawings.
SONG_BUDGET = SceneBudget(fps=30, max_events=1_000_000, max_bytes=64 * 1024 * 1024)


def encode_ass(scene: Scene) -> str:
    return scene.compile(budget=SONG_BUDGET, native_tweens=True).document.dumps()
