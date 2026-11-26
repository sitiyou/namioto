"""Timing preferences shared by the ASS exporter and editor."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ExportSettings:
    offset_ms: int = 0


def validate_settings(settings: ExportSettings) -> None:
    if type(settings.offset_ms) is not int or not -2147483647 <= settings.offset_ms <= 2147483647:
        raise ValueError("offset_ms must be an integer between -2147483647 and 2147483647")
