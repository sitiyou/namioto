# SPDX-License-Identifier: AGPL-3.0-only
"""Karaoke: the `.krc` lyric parser, and the renderers built on it."""

from namioto.karaoke.model import Chapter, KrcError, Line, Lyrics, Ruby, Word
from namioto.karaoke.parser import parse
from namioto.karaoke.transforms import flatten_ruby, merge_words

__all__ = [
    "Chapter",
    "KrcError",
    "Line",
    "Lyrics",
    "Ruby",
    "Word",
    "flatten_ruby",
    "merge_words",
    "parse",
]
