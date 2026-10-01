# SPDX-License-Identifier: AGPL-3.0-only
"""Karaoke: the `.krc` lyric model, and what reads, writes and times it.

`model.py` holds the tree (`Word`/`Ruby`/`Line`/`Chapter`/`Lyrics`), `parser.parse` reads a `.krc`
into it, `writer.dumps` writes an edited tree back out, `transforms` holds the optional passes over
one, and `timeline` turns a parsed lyric into the sounds the editor draws and the aligner times,
and `ass` writes the subtitle those sounds time. The whole package is Qt-free; the names it
re-exports are its public surface.
"""

from namioto.karaoke.ass import AssSettings, generate_ass
from namioto.karaoke.model import Chapter, Group, KrcError, Line, Lyrics, Ruby, Unit, Word
from namioto.karaoke.parser import parse
from namioto.karaoke.timeline import (
    Placement,
    Sound,
    SoundLine,
    align_tokens,
    assign_by_order,
    assign_by_time,
    conflicts,
    contiguous,
    export_krc,
    group_sounds,
    map_faithful,
    map_sounds,
    note_counts,
    snap_to_beats,
    sound_lines,
    sound_ok,
    split,
    text_key,
    with_counts,
)
from namioto.karaoke.transforms import flatten_ruby, merge_words
from namioto.karaoke.writer import dumps

__all__ = [
    "AssSettings",
    "Chapter",
    "Group",
    "KrcError",
    "Line",
    "Lyrics",
    "Sound",
    "SoundLine",
    "Placement",
    "Ruby",
    "Unit",
    "Word",
    "align_tokens",
    "assign_by_order",
    "assign_by_time",
    "conflicts",
    "contiguous",
    "dumps",
    "export_krc",
    "flatten_ruby",
    "generate_ass",
    "group_sounds",
    "map_faithful",
    "map_sounds",
    "merge_words",
    "sound_lines",
    "sound_ok",
    "note_counts",
    "parse",
    "snap_to_beats",
    "split",
    "text_key",
    "with_counts",
]
