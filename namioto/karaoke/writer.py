# SPDX-License-Identifier: AGPL-3.0-only
"""Write a `Lyrics` model back out as `.krc` text.

The model is merged, so the writer restores only what re-parses to it: a word that would not come
back as one written-out word is wrapped in `(...)`, and a `.N` is written when its mora differs from
the mora it would have without one. Comments, blank lines and the original spacing are gone; the
model is what survives.

Qt-free.
"""

from __future__ import annotations

from namioto.karaoke.model import Chapter, KrcError, Line, Lyrics, Word

SEPARATOR = "\n---\n"
META = set("#\n[]().-,{}\r\f\xa0")


def dumps(lyrics: Lyrics) -> str:
    """Serialise the model to `.krc` text; a word that cannot be written raises `KrcError`."""
    return SEPARATOR.join(_chapter_text(chapter) for chapter in lyrics.chapters)


def _chapter_text(chapter: Chapter) -> str:
    return "\n".join(_line_text(line) for line in chapter.lines)


def _line_text(line: Line) -> str:
    body = "".join(_word_text(word) for word in line.words)
    return f"{{{line.track}}}{body}" if line.track != 1 else body


def _word_text(word: Word) -> str:
    _check_text(word.text)
    text = f"({word.text})" if _needs_group(word) else word.text
    if word.ruby is not None:
        ruby = ",".join(_ruby_text(part) for part in word.ruby.parts)
        text += f"[{ruby}]"
    if word.override is not None and word.mora != word.natural_mora:
        text += f".{word.mora}"
    return text


def _ruby_text(part: list[Word]) -> str:
    if not part:
        raise KrcError("a ruby part may not be empty")
    return "".join(_word_text(inner) for inner in part)


def _needs_group(word: Word) -> bool:
    """Whether the text must be parenthesised to come back as one word."""
    if word.grouped:
        return True
    if len(word.text) <= 1 or word.is_latin():
        return False
    return not (word.ruby is not None and word.is_kanji())


def _check_text(text: str) -> None:
    if not text:
        raise KrcError("a word's text may not be empty")
    forbidden = META.intersection(text)
    if forbidden:
        raise KrcError(f"a word's text may not hold {sorted(forbidden)!r}")
