"""A Hook recipe using Namioto’s default layout and the project’s channel colours."""

from dataclasses import replace

from namioto.karaoke.default_template import default_scene
from namioto.params import Field


def build(song, values):
    settings = replace(song.settings, base_color=values["ink"], pulse=values["pulse"])
    return default_scene(replace(song, settings=settings))


def register(registry):
    registry.add(
        id="cool-ink",
        title="Cool ink",
        formats=("hook",),
        parameters=(
            Field("ink", "color", "CDEEFF", "Fill before the wipe"),
            Field("pulse", "float", 1.04, "Scale during the wipe", low=1, high=1.3),
        ),
        build=build,
    )
