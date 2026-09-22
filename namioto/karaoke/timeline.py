# SPDX-License-Identifier: AGPL-3.0-only
"""The `.krc` as a timeline: one mora per unit, each carrying the one token the aligner reads.

`mora_lines` turns the parsed lyrics into rows of morae for the strip, and `align_tokens` flattens
them into the single token stream `namioto.align` reads. A token here is a per-mora refinement of
`namioto.utils.kana_tokens`: the characters are the same and in the same order, so the forced
alignment is unchanged, but a long vowel or a sokuon gets a token of its own and so a time of its
own. A kanji the `.krc` never gave a ruby raises, because there is no sound to align it to.

`split` folds the aligner's flat token stream back onto the lines, `note_counts`/`conflicts` judge
the times against the notes, and `with_counts` writes the counts back out as `.N`.

Qt-free.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass

from namioto.karaoke.model import KrcError, Line, Word
from namioto.karaoke.parser import parse
from namioto.karaoke.writer import dumps
from namioto.utils import kana_tokens

SMALL_KANA = frozenset("ャュョァィゥェォゃゅょぁぃぅぇぉゎヮ")
OWN_MORA = frozenset("ーっッ")
# how far a mora's time may sit from the note it should start and end on, one aligner frame of slack
TOLERANCE = 0.05


@dataclass(frozen=True)
class Mora:
    """One mora: the surface it shows, its kana, and the one token the aligner reads for it."""

    base: str
    ruby: str
    token: str


@dataclass(frozen=True)
class MoraLine:
    """One lyric line as its morae, in order."""

    text: str
    morae: tuple[Mora, ...]


def mora_lines(text: str) -> list[MoraLine]:
    """Every line of a `.krc`, as its morae.

    A kanji without a ruby, or a long vowel or sokuon with no mora to lean on, raises `KrcError`.
    """
    return [_row(line)[0] for chapter in parse(text).chapters for line in chapter.lines]


def align_tokens(lines: list[MoraLine]) -> list[str]:
    """The token stream the aligner reads: every line's morae, in order, one token each."""
    return [mora.token for line in lines for mora in line.morae]


def text_key(text: str) -> str:
    """A short key for a `.krc` text, so the times stored beside it can tell when it changed."""
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def split(tokens: Sequence, lines: list[MoraLine]) -> list[list[tuple[float | None, float | None]]]:
    """The aligner's flat tokens, one per mora in order, cut back into the lines' rows."""
    rows = []
    at = 0
    for line in lines:
        row = tokens[at : at + len(line.morae)]
        rows.append([(token.start, token.end) for token in row])
        at += len(row)
    return rows


def snap_to_beats(
    times: list[list[tuple[float | None, float | None]]], bpm: float, division: float = 1.0
) -> list[list[tuple[float | None, float | None]]]:
    """Every mora boundary rounded to the grid of `division` beats at `bpm`, kept in order.

    The boundaries are snapped, not each mora on its own, so two morae keep sharing the line
    between them; a boundary pushed past its neighbour moves one step on so none collapse. A line
    with an unaligned mora is left alone, since its boundaries say nothing yet.
    """
    step = 60.0 / max(bpm, 1.0) * division
    rows = []
    for row in times:
        bounds = ([span[0] for span in row] + [row[-1][1]]) if row else []
        if any(value is None for value in bounds):
            rows.append([tuple(span) for span in row])
            continue
        snapped: list[float] = []
        previous = None
        for value in bounds:
            point = round(value / step) * step
            if previous is not None and point <= previous:
                point = previous + step
            snapped.append(point)
            previous = point
        rows.append([(snapped[i], snapped[i + 1]) for i in range(len(snapped) - 1)])
    return rows


def note_counts(
    lines: list[MoraLine], times: list[list[tuple[float | None, float | None]]], notes: Sequence[tuple[float, float]]
) -> list[list[int]]:
    """How many notes each mora covers: the notes wholly inside its span, gaps allowed."""
    return [[_count(span, notes) for span in row] for row in times]


def conflicts(
    lines: list[MoraLine], times: list[list[tuple[float | None, float | None]]], notes: Sequence[tuple[float, float]]
) -> list[str]:
    """Why the text and the notes do not line up yet: an unaligned, unbacked or shared mora."""
    problems = []
    claimed: dict[int, str] = {}
    for line, row in zip(lines, times, strict=True):
        if len(row) != len(line.morae):
            problems.append(f"'{line.text}' has {len(line.morae)} morae but {len(row)} times")
            continue
        for mora, (start, end) in zip(line.morae, row, strict=True):
            label = mora.base or mora.ruby
            if start is None or end is None:
                problems.append(f"{label}: not aligned")
                continue
            inside = [index for index, note in enumerate(notes) if _inside(note, start, end)]
            if not inside:
                problems.append(f"{label}: no note between {start:.3f} and {end:.3f}")
                continue
            first, last = notes[inside[0]], notes[inside[-1]]
            if abs(first[0] - start) > TOLERANCE or abs(last[1] - end) > TOLERANCE:
                problems.append(f"{label}: does not start and end on its notes")
            for index in inside:
                if index in claimed:
                    problems.append(f"{label}: shares a note with {claimed[index]}")
                else:
                    claimed[index] = label
    return problems


def with_counts(text: str, counts: list[list[int]]) -> str:
    """A `.krc` with each mora's `.N` set to its note count; a count equal to the reading gets none."""
    lyrics = parse(text)
    index = 0
    for chapter in lyrics.chapters:
        for line in chapter.lines:
            _line, sources = _row(line)
            for source, count in zip(sources, counts[index], strict=True):
                source.override = None if count == source.natural_mora else count
            index += 1
    return dumps(lyrics)


def _inside(note: tuple[float, float], start: float, end: float) -> bool:
    return note[0] >= start - TOLERANCE and note[1] <= end + TOLERANCE


def _count(span: tuple[float | None, float | None], notes: Sequence[tuple[float, float]]) -> int:
    start, end = span
    if start is None or end is None:
        return 0
    return sum(1 for note in notes if _inside(note, start, end))


def _row(line: Line) -> tuple[MoraLine, list[Word]]:
    text = "".join(word.text for word in line.words)
    unread = [word.text for word in line.words if word.ruby is None and word.is_kanji()]
    if unread:
        raise KrcError(f"'{text}' has kanji with no ruby to align: {' '.join(unread)}")

    units: list[list[str]] = []
    sources: list[Word] = []
    for word in line.words:
        for base, kana, source in _units(word):
            if kana in SMALL_KANA:
                if units:
                    units[-1][1] += kana
                continue
            if word.natural_mora == 0:
                continue
            units.append([base, kana])
            sources.append(source)

    folded = [char for token in kana_tokens("".join(kana for _base, kana in units)) for char in token]
    sizes = [_size(kana) for _base, kana in units]
    if sum(sizes) != len(folded):
        raise KrcError(f"'{text}' has a long vowel or a sokuon with no sound to lean on")

    morae = []
    at = 0
    for (base, kana), size in zip(units, sizes, strict=True):
        morae.append(Mora(base, kana, "".join(folded[at : at + size])))
        at += size
    return MoraLine(text, tuple(morae)), sources


def _units(word: Word) -> list[tuple[str, str, Word]]:
    """`(surface, kana, source)` per unit: a plain word is one, a ruby is one per mora."""
    if word.ruby is None:
        return [(word.text, word.text, word)]
    one_part = len(word.ruby.parts) == 1
    units = []
    for index, part in enumerate(word.ruby.parts):
        surface = word.text if one_part else word.text[index : index + 1]
        for position, inner in enumerate(part):
            units.append((surface if position == 0 else "", inner.text, inner))
    return units


def _size(kana: str) -> int:
    """How many characters this mora owns in the folded stream; a long vowel or sokuon owns one."""
    if kana in OWN_MORA:
        return 1
    return len("".join(kana_tokens(kana)))
