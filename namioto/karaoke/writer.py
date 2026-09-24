# SPDX-License-Identifier: AGPL-3.0-only
"""Write a `Lyrics` model back out as `.krc` text.

A group is written between `(...)`, a single word plain; the model is what survives, and a word
that cannot be written raises `KrcError`. Comments, blank lines and the original spacing are gone.

Qt-free.
"""

from __future__ import annotations

from namioto.karaoke.model import Chapter, Group, KrcError, Line, Lyrics, Unit

SEPARATOR = "\n---\n"
META = set("#\n[]().-,{}\r\f\xa0")


def dumps(lyrics: Lyrics) -> str:
    """Serialise the model to `.krc` text; a word that cannot be written raises `KrcError`."""
    return SEPARATOR.join(_chapter_text(chapter) for chapter in lyrics.chapters)


def _chapter_text(chapter: Chapter) -> str:
    return "\n".join(_line_text(line) for line in chapter.lines)


def _line_text(line: Line) -> str:
    body = "".join(_unit_text(unit) for unit in line.units)
    return f"{{{line.track}}}{body}" if line.track != 1 else body


def _unit_text(unit: Unit) -> str:
    _check_text(unit.text)
    text = f"({unit.text})" if _needs_group(unit) else unit.text
    if unit.ruby is not None:
        ruby = ",".join(_ruby_text(part) for part in unit.ruby.parts)
        text += f"[{ruby}]"
    if unit.override is not None and unit.mora != unit.natural_mora:
        text += f".{unit.mora}"
    return text


def _needs_group(unit: Unit) -> bool:
    """Whether the text must be parenthesised to come back as one unit."""
    if isinstance(unit.base, Group) and unit.base.explicit:
        return True
    if len(unit.text) <= 1 or unit.is_latin():
        return False
    return not (unit.ruby is not None and unit.is_kanji())


def _ruby_text(part: list[Unit]) -> str:
    if not part:
        raise KrcError("a ruby part may not be empty")
    return "".join(_unit_text(inner) for inner in part)


def _check_text(text: str) -> None:
    if not text:
        raise KrcError("a word's text may not be empty")
    forbidden = META.intersection(text)
    if forbidden:
        raise KrcError(f"a word's text may not hold {sorted(forbidden)!r}")
