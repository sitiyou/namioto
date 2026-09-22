# SPDX-License-Identifier: AGPL-3.0-only
"""Karaoke: the `.krc` lyric parser, and the renderers built on it."""

from namioto.karaoke.model import Chapter, KrcError, Line, Lyrics, Ruby, Word
from namioto.karaoke.parser import parse
from namioto.karaoke.timeline import (
    Mora,
    MoraLine,
    align_tokens,
    conflicts,
    mora_lines,
    note_counts,
    snap_to_beats,
    split,
    text_key,
    with_counts,
)
from namioto.karaoke.transforms import flatten_ruby, merge_words
from namioto.karaoke.writer import dumps

__all__ = [
    "Chapter",
    "KrcError",
    "Line",
    "Lyrics",
    "Mora",
    "MoraLine",
    "Ruby",
    "Word",
    "align_tokens",
    "conflicts",
    "dumps",
    "flatten_ruby",
    "merge_words",
    "mora_lines",
    "note_counts",
    "parse",
    "snap_to_beats",
    "split",
    "text_key",
    "with_counts",
]
