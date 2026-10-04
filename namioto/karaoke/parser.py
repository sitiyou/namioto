# SPDX-License-Identifier: AGPL-3.0-only
"""The parser for a `.krc`: its chapters, lines, words, ruby and mora.

The grammar is the file's whole syntax. `---` splits chapters, `{N}` tags a line with a track,
`漢字[かんじ]` and `(word)[ruby]` attach a ruby, a comma splits that ruby per character,
`.N` overrides a word's mora count, `.+` joins its Sounds to the preceding NOTE, and `#` starts a
comment. A word carries the mora its ruby gives it; a run of kanji before a ruby is merged into
the word the ruby belongs to, so a bare
`季節[き,せつ]` reads the same as `(季節)[き,せつ]`, and a run of Latin letters (half- or full-width)
is one word of one mora.

`parse()` builds the model in `model.py` and validates it. Qt-free.
"""

from __future__ import annotations

from lark import Lark, Transformer
from lark.exceptions import LarkError, VisitError

from namioto.karaoke.model import Chapter, Group, KrcError, Line, Lyrics, Ruby, Unit, Word, _has_join
from namioto.karaoke.transforms import merge_words

GRAMMAR = r"""
start: _NEWLINE? chapter (_SEP chapter)*
chapter: chapter_line (_NEWLINE chapter_line)* _NEWLINE?
chapter_line: line_tag? words
line_tag: "{" INTEGER "}"
words: (word|word_ruby|word_mora|word_join)+
ruby_line: words ("," words)*

word: WORD | OPEN WORD+ CLOSE
word_ruby: word "[" ruby_line "]"
word_mora: (word|word_ruby) "." INTEGER
word_join: (word|word_ruby) "." "+"
OPEN: "("
CLOSE: ")"

COMMENT: "#" /[^\n]/*
WORD: /[^#\n\[\]\(\)\.\-\,{}]/
INTEGER: /\d+/

_NEWLINE: /\n+/
# one token for a chapter break, its leading newline included, so the grammar stays LALR: split off,
# the newline is the lookahead for both ending a chapter and continuing it
_SEP: /\n+-{3,}-*\n*/

%ignore /[\f\r\xa0]/
%ignore COMMENT
"""


class LyricsTransformer(Transformer):
    """Builds the model out of the parse tree."""

    def line_tag(self, items):
        return int(items[0].value)

    def chapter_line(self, items):
        if len(items) == 1:
            return Line(items[0])
        track, units = items
        return Line(units, track=track)

    def words(self, items):
        return items

    def ruby_line(self, items):
        return Ruby(items)

    def word(self, items):
        chars = [str(item) for item in items if item.type not in ("OPEN", "CLOSE")]
        if any(item.type in ("OPEN", "CLOSE") for item in items):
            return Unit(Group([Word(char) for char in chars], explicit=True))
        return Unit(Word(chars[0]))

    def chapter(self, items):
        return Chapter(items)

    def word_ruby(self, items):
        word, ruby = items
        word.set_ruby(ruby)
        return word

    def word_mora(self, items):
        word, mora = items
        word.override_mora(int(mora.value))
        return word

    def word_join(self, items):
        word = items[0]
        word.join_previous = True
        return word

    def start(self, items):
        return Lyrics(items)


parser = Lark(GRAMMAR, start="start", parser="lalr")


def parse(text: str, merge: bool = True) -> Lyrics:
    """Parse a `.krc` into its model; a malformed file raises `KrcError`."""
    try:
        tree = parser.parse(text)
    except LarkError as error:
        raise KrcError(str(error)) from None
    try:
        lyrics = LyricsTransformer().transform(tree)
    except VisitError as error:
        if isinstance(error.orig_exc, ValueError):
            raise error.orig_exc from None
        raise
    if merge:
        return merge_words(lyrics)
    if any(_has_join(unit) for chapter in lyrics.chapters for line in chapter.lines for unit in line.units):
        merge_words(lyrics)
    return lyrics
