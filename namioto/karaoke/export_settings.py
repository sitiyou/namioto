"""Rendering controls shared by the default recipe and editor."""

import colorsys
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

FALLBACK_COLOR = "0000FF"
TRACK_STYLES = {"default": ("K1", "K2"), "top": ("H1", "H2")}


@dataclass(frozen=True)
class ExportSettings:
    """Export preferences; colours are RGB hex, with automatic blur colours."""

    fade_in_ms: int = 800
    fade_out_ms: int = 200
    border: int = 5
    border_furi: int = 3
    ruby_offset: float = 6
    blur: int = 10
    blur_scale: float = 2
    use_project_colors: bool = True
    layout: str = "double"
    base_color: str = "FFFFFF"
    ruby_ratio: float = 0.5
    show_ruby: bool = True
    ruby_font: str = ""
    ruby_color: str = ""
    cue_color: str = ""
    cue: str = "dots"
    cue_placement: str = "independent"
    cue_direction: Literal["right_to_left", "left_to_right"] = "left_to_right"
    cue_size_ratio: float = 0.5
    cue_gap: float = -1
    harmony_tracks: str = ""
    harmony_ratio: float = 1
    harmony_align: Literal["left", "center", "right"] = "center"
    row_gap: float = 0
    feather: float = 0
    pulse: float = 1.1
    pulse_peak: float = 0.35
    hold_ms: int = 0
    font: str = "sans-serif"
    overlay_color: str = FALLBACK_COLOR
    lead_time_ms: int = 5000
    guide_dot_duration_ms: int = 0
    offset_ms: int = 0
    font_size: int = 96
    spacing: float = 5
    margin_h: int = 64
    margin_v: int = 48
    base_outline_color: str = "222222"
    overlay_outline_color: str = "EFEFEF"
    overlay_blur_color: str = ""
    base_blur_color: str = ""
    track_style: Mapping[int, str] = field(default_factory=dict)
    channel_colors: Mapping[int, str] | str = ""
    palt: bool = False

    def vocal_roles(self) -> dict[int, str]:
        roles = {}
        for entry in self.harmony_tracks.split(","):
            if not entry.strip():
                continue
            if not entry.strip().isdigit() or not 0 <= int(entry) < 16:
                raise ValueError("harmony_tracks must contain MIDI channel numbers 0..15")
            roles[int(entry)] = "top"
        for track, role in self.track_style.items():
            if role not in ("default", "top"):
                raise ValueError(f"unknown track style {role!r}")
            roles[int(track)] = role
        return roles

    def channel_colours(self) -> dict[int, str]:
        if isinstance(self.channel_colors, str):
            colours = {}
            for entry in self.channel_colors.split(","):
                if not entry.strip():
                    continue
                number, separator, colour = entry.partition("=")
                if not separator or not number.strip().isdigit():
                    raise ValueError("channel colours must use channel=RRGGBB pairs")
                colours[int(number)] = colour.strip()
        else:
            colours = dict(self.channel_colors)
        for channel, colour in colours.items():
            if isinstance(channel, bool) or not isinstance(channel, int) or not 0 <= channel < 16:
                raise ValueError("a colour channel must be between 0 and 15")
            if not isinstance(colour, str):
                raise ValueError("channel colours must be six-digit RGB colours")
            colour = colour.lstrip("#")
            if len(colour) != 6 or any(char not in "0123456789abcdefABCDEF" for char in colour):
                raise ValueError("channel colours must be six-digit RGB colours")
            colours[channel] = colour.upper()
        return colours

    def blur_colours(self) -> tuple[str, str]:
        overlay = _to_ass(self.overlay_color)
        overlay_blur = _to_ass(self.overlay_blur_color) if self.overlay_blur_color else _shade(overlay, 0.6, 1)
        base = _to_ass(self.base_blur_color) if self.base_blur_color else _shade(overlay_blur, 0.618, 0.618, 0.5)
        return _to_ass(overlay_blur), _to_ass(base)


def _to_ass(rgb: str) -> str:
    text = str(rgb).strip().lstrip("#")
    if len(text) != 6 or any(char not in "0123456789abcdefABCDEF" for char in text):
        text = FALLBACK_COLOR
    return text[4:] + text[2:4] + text[:2]


def _shade(bgr: str, saturation_scale: float, value: float, hue_offset: float = 0) -> str:
    blue, green, red = (int(bgr[at : at + 2], 16) / 255 for at in (0, 2, 4))
    hue, saturation, _ = colorsys.rgb_to_hsv(red, green, blue)
    red, green, blue = (
        round(channel * 255)
        for channel in colorsys.hsv_to_rgb((hue + hue_offset) % 1, min(1, saturation * saturation_scale), value)
    )
    return f"{blue:02X}{green:02X}{red:02X}"
