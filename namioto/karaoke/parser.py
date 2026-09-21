# SPDX-License-Identifier: AGPL-3.0-only
"""The parser for a `.krc`: its chapters, lines, words, ruby and mora.

The grammar is the file's whole syntax. `---` splits chapters, `{N}` tags a line with a track,
`漢字[かんじ]` and `(word)[reading]` attach a reading, a comma splits that reading per character,
`.N` overrides a word's mora count, and `#` starts a comment. A word carries the mora its reading
gives it; a run of kanji before a reading is merged into the word the reading belongs to, so a bare
`季節[き,せつ]` reads the same as `(季節)[き,せつ]`, and a run of Latin letters (half- or full-width)
is one word of one mora.

Qt-free.
"""

from __future__ import annotations

import re
import warnings

from lark import Lark, Token, Transformer
from lark.exceptions import LarkError, VisitError

GRAMMAR = r"""
start: _NEWLINE? chapter (_NEWLINE? "---" "-"* _NEWLINE?  chapter)* _NEWLINE?
chapter: chapter_line (_NEWLINE chapter_line)*
chapter_line: line_tag? line
line_tag: "{" INTEGER "}"
line: (word|word_ruby|word_mora)+
ruby_line: line ("," line)*

word: WORD | "(" WORD+ ")"
word_ruby: word "[" ruby_line "]"
word_mora: (word|word_ruby) "." INTEGER

COMMENT: "#" /[^\n]/*
WORD: /[^#\n\[\]\(\)\.\-\,{}]/
INTEGER: /\d+/

_NEWLINE: /\n+/

%ignore /[\f\r\xa0]/
%ignore COMMENT
"""


def _is_latin(char: str) -> bool:
    return "A" <= char <= "Z" or "a" <= char <= "z" or "Ａ" <= char <= "Ｚ" or "ａ" <= char <= "ｚ"


class Word:
    """One word: its text, its reading, and the mora count the renderer lays out."""

    def __init__(self, text: str, ruby: Line | None = None):
        self.text = text
        self.ruby: Line | None = None
        self.mora: int = self.calc_mora(text)
        self.base_mora = self.mora
        self.is_ruby_mora = False

        if ruby is not None:
            self.set_ruby(ruby)

    def set_ruby(self, ruby: Line):
        if any(word.ruby is not None for word in ruby.words):
            raise ValueError("a reading may not carry a reading of its own")
        assert not self.is_ruby_mora
        self.ruby = ruby
        self.mora = self.ruby.total_mora()
        self.base_mora = self.ruby.total_mora(base=True)
        self.is_ruby_mora = True

    def override_mora(self, mora: int):
        if self.ruby is not None and self.base_mora != self.mora:
            raise ValueError("a reading's mora may not be overridden twice")
        if self.mora == mora:
            warnings.warn(f"overriding mora to the same value {mora} for word '{self.text}'", stacklevel=2)
            return
        self.mora = mora
        self.is_ruby_mora = False

    def is_kanji(self):
        return re.match(r"[\u4e00-\u9faf々]+$", self.text)

    def is_latin(self) -> bool:
        return all(_is_latin(char) for char in self.text)

    def __str__(self) -> str:
        text = self.text
        if self.ruby is not None:
            text += f"[{self.ruby}]"
        if self.mora > 2:
            text += f".{self.mora}"
        return text

    @staticmethod
    def calc_mora(text: str) -> int:
        mora = 0
        latin = False
        for char in text:
            if _is_latin(char):
                if not latin:
                    mora += 1
                latin = True
                continue
            latin = False
            if (
                not char.isprintable()
                or char in "ャュョァィゥェォゃゅょぁぃぅぇぉ 「」『』、。．・♥☆※；…‥？！：（）〔〕“”‘’"
            ):
                continue
            mora += 1
        return mora


class Line:
    """One line of words; `parts` are the words of a reading when the line is one."""

    def __init__(self, words: list[Word], is_ruby=False, track: int = 1):
        self.words: list[Word] = []
        self.is_ruby = is_ruby
        self.parts: list[Line] = [self]
        self.track = track

        while len(words):
            word = words.pop()
            self.words.insert(0, word)
            if word.ruby is None or not word.is_kanji():
                continue
            while len(words):
                previous = words[-1]
                if not (previous.is_kanji() and previous.ruby is None):
                    break
                words.pop()
                word.text = previous.text + word.text

        if not is_ruby:
            self._merge_latin()

    def _merge_latin(self):
        words: list[Word] = []
        for word in self.words:
            if words and word.is_latin() and words[-1].is_latin() and words[-1].ruby is None:
                previous = words.pop()
                override = previous.mora if previous.mora != previous.base_mora else None
                word.text = previous.text + word.text
                if word.ruby is None:
                    if word.mora != word.base_mora:
                        override = word.mora
                    word.base_mora = Word.calc_mora(word.text)
                    word.mora = override if override is not None else word.base_mora
            words.append(word)
        self.words = words

    @classmethod
    def from_ruby_lines(cls, ruby_lines: list[Line]):
        words: list[Word] = []
        for ruby_line in ruby_lines:
            words.extend(ruby_line.words)
        line = cls(words, is_ruby=True)
        line.parts = ruby_lines
        return line

    def total_mora(self, base=False) -> int:
        return sum(word.base_mora if base else word.mora for word in self.words)

    def __str__(self) -> str:
        text = "".join(str(word) for word in self.words)
        return text if self.is_ruby else f"{text} | {self.total_mora()}"

    def flatten_ruby(self):
        words: list[Word] = []
        for word in self.words:
            if word.ruby is None or not word.is_ruby_mora:
                words.append(word)
                continue

            if word.mora == 1:
                assert len(word.ruby.parts) == 1
                part = word.ruby.parts[0]
                if len(part.words) == 1:
                    words.append(word)
                    continue

            for part_idx, part in enumerate(word.ruby.parts):
                part_text = word.text if len(word.ruby.parts) == 1 else word.text[part_idx]
                for i, inner in enumerate(part.words):
                    text = ("#" * bool(part_idx != 0) + part_text) if i == 0 else "#"
                    flat = Word(text)
                    flat.set_ruby(Line([inner], is_ruby=True))
                    words.append(flat)
        self.words = words


class Chapter:
    """A chapter: its lines, each with the track it is sung on."""

    def __init__(self, lines: list[Line]):
        self.lines = lines
        self.verify_ruby()

    def verify_ruby(self):
        for line in self.lines:
            for word in line.words:
                if word.ruby is not None and len(word.ruby.parts) > 1 and len(word.ruby.parts) != len(word.text):
                    raise ValueError(
                        f"the reading of '{word.text}' has {len(word.ruby.parts)} parts for {len(word.text)} characters"
                    )

    def __str__(self) -> str:
        return "\n".join(str(line) for line in self.lines)

    def flatten_ruby(self):
        for line in self.lines:
            line.flatten_ruby()


class Lyrics:
    """A whole `.krc`: its chapters."""

    def __init__(self, chapters: list[Chapter]):
        self.chapters = chapters

    def __str__(self) -> str:
        return "\n----\n".join(str(chapter) for chapter in self.chapters)

    def flatten_ruby(self):
        for chapter in self.chapters:
            chapter.flatten_ruby()


class LyricsTransformer(Transformer):
    """Builds the model out of the parse tree."""

    def line_tag(self, items):
        assert isinstance(items[0], Token) and items[0].type == "INTEGER"
        return int(items[0].value)

    def chapter_line(self, items):
        if len(items) == 1:
            return items[0]
        track, line = items
        assert isinstance(track, int) and isinstance(line, Line)
        line.track = track
        return line

    def line(self, items):
        return Line(items)

    def ruby_line(self, items):
        assert all(isinstance(item, Line) for item in items)
        return Line.from_ruby_lines(items)

    def word(self, items):
        return Word("".join(items))

    def chapter(self, items):
        return Chapter(items)

    def word_ruby(self, items):
        assert len(items) == 2
        word, ruby = items
        assert isinstance(word, Word) and isinstance(ruby, Line)
        ruby.is_ruby = True
        word.set_ruby(ruby)
        return word

    def word_mora(self, items):
        assert len(items) == 2
        word, mora = items
        assert isinstance(word, Word) and isinstance(mora, Token) and mora.type == "INTEGER"
        if word.ruby is not None:
            assert word.base_mora == word.mora
        word.override_mora(int(mora.value))
        return word

    def start(self, items):
        return Lyrics(items)


parser = Lark(GRAMMAR, start="start")


def parse(text: str) -> Lyrics:
    """Parse a `.krc` into its model; a malformed file raises `ValueError`."""
    try:
        tree = parser.parse(text)
    except LarkError as error:
        raise ValueError(str(error)) from None
    try:
        return LyricsTransformer().transform(tree)
    except VisitError as error:
        if isinstance(error.orig_exc, ValueError):
            raise error.orig_exc from None
        raise
