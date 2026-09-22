# SPDX-License-Identifier: AGPL-3.0-only
"""The `.krc` model: words, rubies, lines, chapters and the mora they add up to.

A word carries its text, the ruby (`Ruby`) it may have and the `.N` that was written for it, if any.
Mora is never stored - `Word.mora`, `Word.base_mora` and `Ruby.total_mora` are derived - so a change
to the text, the ruby or the override is reflected everywhere at once.

Qt-free.
"""

from __future__ import annotations

import re
import unicodedata
import warnings
from dataclasses import dataclass

KANJI = re.compile(r"[\u4e00-\u9faf々]+$")
LATIN = re.compile(r"[A-Za-zＡ-Ｚａ-ｚ]+")
IGNORED = "ャュョァィゥェォゃゅょぁぃぅぇぉゎヮ 「」『』、。．・♥☆※；…‥？！：（）〔〕“”‘’"


class KrcError(ValueError):
    """A `.krc` that cannot be parsed or does not hold together."""


def calc_mora(text: str) -> int:
    """One mora per printable character that carries a sound; punctuation and symbols carry none."""
    mora = 0
    for char in text:
        if not char.isprintable() or char in IGNORED:
            continue
        if unicodedata.category(char)[0] in ("P", "S"):
            continue
        mora += 1
    return mora


@dataclass
class Word:
    """One word: its text, its ruby, the mora its `.N` forces and whether it was grouped."""

    text: str
    ruby: Ruby | None = None
    override: int | None = None
    grouped: bool = False

    @property
    def base_mora(self) -> int:
        return calc_mora(self.text) if self.ruby is None else self.ruby.total_mora(base=True)

    @property
    def natural_mora(self) -> int:
        """The mora the text or the ruby gives, before a written `.N`, a Latin run counting one."""
        if self.ruby is not None:
            return self.ruby.total_mora()
        return 1 if self.is_latin() else calc_mora(self.text)

    @property
    def mora(self) -> int:
        return self.override if self.override is not None else self.natural_mora

    @property
    def is_ruby_mora(self) -> bool:
        return self.ruby is not None and self.override is None

    def set_ruby(self, ruby: Ruby) -> None:
        if any(word.ruby is not None for part in ruby.parts for word in part):
            raise KrcError("a ruby may not carry a ruby of its own")
        self.ruby = ruby

    def override_mora(self, mora: int) -> None:
        if self.ruby is not None and self.base_mora != self.mora:
            raise KrcError("a ruby's mora may not be overridden twice")
        if self.base_mora == mora:
            warnings.warn(f"overriding mora to the same value {mora} for word '{self.text}'", stacklevel=2)
        self.override = mora

    def is_kanji(self) -> bool:
        return bool(KANJI.match(self.text))

    def is_latin(self) -> bool:
        return bool(LATIN.fullmatch(self.text))

    def __str__(self) -> str:
        text = self.text
        if self.ruby is not None:
            text += f"[{self.ruby}]"
        if self.mora > 2:
            text += f".{self.mora}"
        return text


@dataclass
class Ruby:
    """The ruby of a word: its comma-separated parts, each a run of words."""

    parts: list[list[Word]]

    def total_mora(self, base: bool = False) -> int:
        return sum(word.base_mora if base else word.mora for part in self.parts for word in part)

    def __str__(self) -> str:
        return "".join(str(word) for part in self.parts for word in part)


@dataclass
class Line:
    """One line of words, with the track it is sung on."""

    words: list[Word]
    track: int = 1

    def total_mora(self, base: bool = False) -> int:
        return sum(word.base_mora if base else word.mora for word in self.words)

    def __str__(self) -> str:
        text = "".join(str(word) for word in self.words)
        return f"{text} | {self.total_mora()}"


@dataclass
class Chapter:
    """A chapter: its lines, each with the track it is sung on."""

    lines: list[Line]

    def __str__(self) -> str:
        return "\n".join(str(line) for line in self.lines)


@dataclass
class Lyrics:
    """A whole `.krc`: its chapters."""

    chapters: list[Chapter]

    def __str__(self) -> str:
        return "\n----\n".join(str(chapter) for chapter in self.chapters)


def validate(lyrics: Lyrics) -> None:
    for chapter in lyrics.chapters:
        for line in chapter.lines:
            for word in line.words:
                if word.ruby is not None and len(word.ruby.parts) > 1 and len(word.ruby.parts) != len(word.text):
                    raise KrcError(
                        f"the ruby of '{word.text}' has {len(word.ruby.parts)} parts for {len(word.text)} characters"
                    )
