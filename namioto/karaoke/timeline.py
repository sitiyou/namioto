# SPDX-License-Identifier: AGPL-3.0-only
"""The `.krc` as a timeline: one mora per unit, each carrying the one token the aligner reads.

`mora_lines` turns the parsed lyrics into rows of morae for the strip, and `align_tokens` flattens
them into the single token stream `namioto.analysis.align` reads. A token here is a per-mora refinement of
`namioto.utils.kana_tokens`: the characters are the same and in the same order, so the forced
alignment is unchanged, but a long vowel or a sokuon gets a token of its own and so a time of its
own. A kanji the `.krc` never gave a ruby raises, because there is no sound to align it to.

`split` folds the aligner's flat token stream back onto the lines, `note_counts`/`conflicts` judge
the times against the notes, and `with_counts` writes the counts back out as `.N`.

Qt-free.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence
from dataclasses import dataclass

from namioto.karaoke.model import Group, KrcError, Line, Unit, Word
from namioto.karaoke.parser import parse
from namioto.karaoke.writer import dumps
from namioto.utils import kana_tokens

SMALL_KANA = frozenset("ャュョァィゥェォゃゅょぁぃぅぇぉゎヮ")
OWN_MORA = frozenset("ーっッ")
# how far a mora's time may sit from the note it should start and end on, one aligner frame of slack
TOLERANCE = 0.05
# how close the shares of a shared note must be for its morae to group, and how large each must stay
EQUAL_BAND = 0.2
SHARE = 0.4


@dataclass(frozen=True)
class Mora:
    """One mora: the surface it belongs to, the kana it reads, and the token the aligner reads.

    A mora of a ruby-bearing word carries the base unit it is read from - the whole run of kanji
    when the `.krc` gives one ruby for it, or one kanji of a comma-separated ruby - and whether that
    unit is the word's first, which `label` turns into its parentheses or its brackets.
    """

    base: str
    ruby: str
    token: str
    rubied: bool = False
    first: bool = True

    @property
    def label(self) -> str:
        """What a block shows: the word itself without a ruby, else the ruby beside its base unit."""
        if not self.rubied:
            return self.base
        left, right = ("(", ")") if self.first else ("[", "]")
        return f"{self.ruby}{left}{self.base}{right}"


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
    times: list[list[tuple[float | None, float | None]]], bpm: float, division: float = 1.0, offset: float = 0.0
) -> list[list[tuple[float | None, float | None]]]:
    """Every mora's start and end rounded to the grid of `division` beats at `bpm` off `offset`.

    The grid is the one that is drawn: `offset` is the editor's slid grid, 0 the absolute beats. A
    mora rounds on its own, so one whose two ends land in the same cell comes back with no length -
    a mora nothing is sung on, which the strip and the conflict check already read - rather than
    pushing the rest of its line one cell per collision off the beat. A line with an unaligned mora
    is left alone, since its boundaries say nothing yet.
    """
    step = 60.0 / max(bpm, 1.0) * division
    rows = []
    for row in times:
        if any(start is None or end is None for start, end in row):
            rows.append([tuple(span) for span in row])
            continue
        snapped = []
        for start, end in row:
            start = offset + round((start - offset) / step) * step
            end = offset + round((end - offset) / step) * step
            snapped.append((start, max(start, end)))
        rows.append(snapped)
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
            label = mora.label
            if start is None or end is None:
                problems.append(f"{label}: not aligned")
                continue
            if end <= start:
                continue  # a mora of no length has nothing to sit on
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


def mora_ok(
    lines: list[MoraLine], times: list[list[tuple[float | None, float | None]]], notes: Sequence[tuple[float, float]]
) -> list[list[bool]]:
    """Whether each mora sits on its own notes, the same judgement `conflicts` reports by hand.

    A mora is not settled when it is unaligned, backs no note, does not start and end on its
    boundary notes, or shares a note with another mora.
    """
    ok = []
    holders: dict[int, list[tuple[int, int]]] = {}
    for line, row in zip(lines, times, strict=True):
        flags = []
        for column, (start, end) in enumerate(row):
            if start is not None and end is not None and end <= start:
                flags.append(len(row) == len(line.morae))  # a mora of no length has nothing to sit on
                continue
            inside = (
                [index for index, note in enumerate(notes) if _inside(note, start, end)]
                if start is not None and end is not None
                else []
            )
            good = bool(inside) and len(row) == len(line.morae)
            if good:
                good = abs(notes[inside[0]][0] - start) <= TOLERANCE and abs(notes[inside[-1]][1] - end) <= TOLERANCE
            flags.append(good)
            for index in inside:
                holders.setdefault(index, []).append((len(ok), column))
        ok.append(flags)
    for cells in holders.values():
        if len(cells) > 1:
            for row_index, column in cells:
                ok[row_index][column] = False
    return ok


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


def group_morae(
    text: str,
    times: Sequence[Sequence[tuple[float | None, float | None]]],
    row: int,
    first: int,
    last: int,
) -> tuple[str, list[list[tuple[float | None, float | None]]]]:
    """Fold the morae `first..last` of one line into a single word of the `.krc`, written `(...)`.

    A group is one word to the format, so its `.N` counts the notes the whole run covers rather than
    one mora at a time: two morae that share a note stop clashing, and each keeps its own reading
    instead of one of them being squeezed to no length. The run becomes one mora itself, so the
    times come back as the span it covered. The kana is read the same way, so the aligner's token
    stream does not move - a run that would move a sound, a sokuon or a long vowel left leaning on
    nothing, raises `KrcError`.
    """
    lyrics = parse(text)
    lines = [line for chapter in lyrics.chapters for line in chapter.lines]
    if not 0 <= row < len(lines):
        raise KrcError(f"the lyrics have no line {row}")
    line = lines[row]
    _line, sources = _row(line)
    if not 0 <= first < last < len(sources):
        raise KrcError("a group is made of two morae or more of one line")
    indices = []
    for source in sources[first : last + 1]:
        index = next((index for index, word in enumerate(line.words) if word is source), None)
        if index is None:
            raise KrcError("a group cannot take in a mora that a ruby reads")
        indices.append(index)
    # a small kana is no mora of its own: it rides in the word before it, so the range may step
    # over it and the group still takes whole words
    covered = set(indices)
    stepped = (
        line.words[index].text in SMALL_KANA for index in range(indices[0], indices[-1] + 1) if index not in covered
    )
    if not all(stepped):
        raise KrcError("a group takes whole words next to each other")

    tokens = [[mora.token for mora in _row(other)[0].morae] for other in lines]
    tokens[row] = tokens[row][:first] + ["".join(tokens[row][first : last + 1])] + tokens[row][last + 1 :]
    words = line.words[indices[0] : indices[-1] + 1]
    line.words = [
        *line.words[: indices[0]],
        Unit(Group([Word(char) for char in "".join(word.text for word in words)])),
        *line.words[indices[-1] + 1 :],
    ]
    grouped = dumps(lyrics)
    if [[mora.token for mora in read.morae] for read in mora_lines(grouped)] != tokens:
        raise KrcError("folding these morae into one word would move a sound")
    return grouped, _fold_times(times, row, first, last)


def _fold_times(
    times: Sequence[Sequence[tuple[float | None, float | None]]], row: int, first: int, last: int
) -> list[list[tuple[float | None, float | None]]]:
    """The rows of times with one line's run of morae folded into the span the run covered."""
    rows = [list(row_times) for row_times in times]
    if not rows or row >= len(rows):
        return rows
    if last >= len(rows[row]):
        raise KrcError("the times do not count the morae of this line")
    rows[row] = [*rows[row][:first], (rows[row][first][0], rows[row][last][1]), *rows[row][last + 1 :]]
    return rows


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

    units: list[list] = []
    sources: list[Word] = []
    for word in line.words:
        for base, kana, rubied, first, source in _units(word):
            if kana in SMALL_KANA:
                if units:
                    units[-1][1] += kana
                    if not units[-1][2]:
                        # a plain surface takes the small kana too, so its block reads ショ, not シ
                        units[-1][0] += kana
                continue
            if word.natural_mora == 0:
                continue
            units.append([base, kana, rubied, first])
            sources.append(source)

    folded = [char for token in kana_tokens("".join(kana for _base, kana, _rubied, _first in units)) for char in token]
    sizes = [_size(kana) for _base, kana, _rubied, _first in units]
    if sum(sizes) != len(folded):
        raise KrcError(f"'{text}' has a long vowel or a sokuon with no sound to lean on")

    morae = []
    at = 0
    for (base, kana, rubied, first), size in zip(units, sizes, strict=True):
        morae.append(Mora(base, kana, "".join(folded[at : at + size]), rubied, first))
        at += size
    return MoraLine(text, tuple(morae)), sources


def _units(word: Word) -> list[tuple[str, str, bool, bool, Word]]:
    """`(surface, kana, rubied, first, source)` per unit: a plain word is one, a ruby one per mora.

    Every mora of a base unit carries that unit's whole text, and `first` marks the unit the word
    opens with, so a comma-separated `世界[せ,かい]` reads `せ(世)` but `か[界]` and `い[界]`.
    """
    if word.ruby is None:
        return [(word.text, word.text, False, True, word)]
    one_part = len(word.ruby.parts) == 1
    units = []
    for index, part in enumerate(word.ruby.parts):
        surface = word.text if one_part else word.text[index : index + 1]
        for inner in part:
            units.append((surface, inner.text, True, index == 0, inner))
    return units


def _size(kana: str) -> int:
    """How many characters this mora owns in the folded stream; a long vowel or sokuon owns one."""
    if kana in OWN_MORA:
        return 1
    return len("".join(kana_tokens(kana)))


def assign_by_order(blocks: int, notes: int) -> list[int | None]:
    """Pair the blocks and the notes in order, one for one, until the shorter side runs out."""
    return [index if index < notes else None for index in range(blocks)]


def assign_by_time(
    blocks: Sequence[tuple[float | None, float | None]], notes: Sequence[tuple[float, float]]
) -> list[int | None]:
    """Match every block to one note, in order, by the note its aligned onset falls in.

    A block takes the note holding its start, or the nearest note when it lands in a rest, and the
    whole match is the cheapest monotone one: every block gets a note, a note may take several
    blocks, and a note no block reaches is skipped. Monotonicity keeps noisy times from reordering
    the blocks, and the search passes over an instrumental note between two sung ones rather than
    claiming it. None means there was no note at all to give it.
    """
    if not blocks:
        return []
    if not notes:
        return [None] * len(blocks)
    count, total = len(blocks), len(notes)
    inf = math.inf
    reach = [[inf] * (total + 1) for _ in range(count + 1)]  # blocks[:i] among notes[:j], any end
    last = [[inf] * (total + 1) for _ in range(count + 1)]  # the same, with block i-1 on note j-1
    opens = [[False] * (total + 1) for _ in range(count + 1)]  # last came from opening a new note
    takes = [[False] * (total + 1) for _ in range(count + 1)]  # reach came from last, not a skip
    for column in range(total + 1):
        reach[0][column] = 0.0
    for i in range(1, count + 1):
        for j in range(1, total + 1):
            cost = _onset_distance(blocks[i - 1], notes[j - 1])
            grouped = last[i - 1][j] if i > 1 else inf
            opened = reach[i - 1][j - 1]
            opens[i][j] = opened < grouped
            last[i][j] = cost + (opened if opens[i][j] else grouped)
            takes[i][j] = last[i][j] <= reach[i][j - 1]
            reach[i][j] = min(reach[i][j - 1], last[i][j])
    found: list[int | None] = [None] * count
    i, j, on_last = count, total, False
    while i > 0:
        if not on_last:
            if not takes[i][j]:
                j -= 1  # the note takes no block, so step past it
                continue
            on_last = True
            continue
        found[i - 1] = j - 1
        opened = opens[i][j]
        i -= 1
        if opened:
            j -= 1
            on_last = False  # back to the note before, still unassigned
    return found


def _onset_distance(span: tuple[float | None, float | None], note: tuple[float, float]) -> float:
    """How far a block's onset is from a note: zero inside it, else the gap to its nearer edge."""
    start = span[0]
    if start is None:
        return 0.0
    low, high = note
    if low <= start <= high:
        return 0.0
    return min(abs(start - low), abs(start - high))


@dataclass(frozen=True)
class Placement:
    """Where one mora sits once the notes are read: its span, the notes it covers, and its doubt.

    `span` is what the strip draws - the note(s) it covers, a group's slice of a shared note, or a
    point for a mora of no length. `notes` are the note indices it covers, `zero` a mora that fell
    on none, and `red` one whose time the aligner cannot be trusted for.
    """

    span: tuple[float | None, float | None]
    notes: tuple[int, ...] = ()
    zero: bool = False
    red: bool = False


def map_morae(
    lines: Sequence[MoraLine],
    times: Sequence[Sequence[tuple[float | None, float | None]]],
    notes: Sequence[tuple[float, float]],
    text: str = "",
    flagged: Sequence[bool] | None = None,
    *,
    aligned: bool = True,
) -> list[list[Placement]]:
    """Put every mora on the notes its time covers, and settle the notes its neighbours share.

    With `aligned`, a mora covers every note its time overlaps; a note several morae share is
    grouped when the shares are close and the run may be folded into one word, and otherwise all
    but the largest share become morae of no length. A note no mora reaches is given to the one
    before it and doubted, so every note is answered for. Without `aligned` the morae and the notes
    are paired one for one, in reading order, until the shorter side runs out. `flagged` marks the
    lines the aligner itself doubted, which reddens the whole line.
    """
    flat: list[tuple[int, int, float | None, float | None]] = []
    for row, line in enumerate(lines):
        for column, _mora in enumerate(line.morae):
            present = row < len(times) and column < len(times[row])
            span = times[row][column] if present else (None, None)
            flat.append((row, column, span[0], span[1]))

    if not aligned:
        return _by_order(lines, notes, assign_by_order(len(flat), len(notes)))
    if not flat or not notes:
        return [[Placement((None, None)) for _mora in line.morae] for line in lines]

    holders: list[list[int]] = [[] for _note in notes]
    for index, (_row, _column, start, end) in enumerate(flat):
        if start is None or end is None or end < start:
            continue
        for note, (low, high) in enumerate(notes):
            if min(end, high) > max(start, low):
                holders[note].append(index)

    owner: list[list[int]] = [[] for _note in notes]
    doubted: set[int] = set()
    for note, sharers in enumerate(holders):
        if not sharers:
            continue
        if len(sharers) == 1:
            owner[note] = list(sharers)
            continue
        shares = [_share(flat, notes[note], mora) for mora in sharers]
        if max(shares) - min(shares) <= EQUAL_BAND and min(shares) >= SHARE and _groupable(text, times, flat, sharers):
            owner[note] = list(sharers)
        else:
            owner[note] = [sharers[max(range(len(sharers)), key=lambda at: shares[at])]]

    for note in range(len(notes)):
        if owner[note]:
            continue
        before = next((other for other in range(note - 1, -1, -1) if owner[other]), None)
        if before is not None:
            mora = owner[before][-1]
        else:
            after = next((other for other in range(note + 1, len(notes)) if owner[other]), None)
            if after is None:
                continue
            mora = owner[after][0]
        owner[note] = [mora]
        doubted.add(mora)

    covered: dict[int, list[int]] = {}
    for note, owners in enumerate(owner):
        for mora in owners:
            covered.setdefault(mora, []).append(note)

    pieces: dict[int, list[tuple[float, float]]] = {}
    for note, owners in enumerate(owner):
        if not owners:
            continue
        low, high = notes[note]
        if len(owners) == 1:
            pieces.setdefault(owners[0], []).append((low, high))
            continue
        shares = [_share(flat, notes[note], mora) for mora in owners]
        total = sum(shares) or float(len(owners))
        edge = low
        for mora, share in zip(owners, shares, strict=True):
            width = (high - low) * share / total
            pieces.setdefault(mora, []).append((edge, edge + width))
            edge += width

    marked = list(flagged) if flagged is not None else []
    found: list[list[Placement]] = []
    index = 0
    for row, line in enumerate(lines):
        red_line = row < len(marked) and bool(marked[row])
        placements = []
        for _mora in line.morae:
            notes_of = covered.get(index)
            if notes_of:
                chunks = pieces[index]
                span = (min(chunk[0] for chunk in chunks), max(chunk[1] for chunk in chunks))
                placements.append(Placement(span=span, notes=tuple(notes_of), red=red_line or index in doubted))
            else:
                point = _zero_point(flat[index][2], notes)
                placements.append(Placement(span=(point, point), zero=True, red=red_line))
            index += 1
        found.append(placements)
    return found


def _by_order(lines: Sequence[MoraLine], notes: Sequence[tuple[float, float]], found) -> list[list[Placement]]:
    """The morae and the notes matched in reading order, one for one, with nothing doubted."""
    out: list[list[Placement]] = []
    at = 0
    for line in lines:
        row = []
        for _mora in line.morae:
            index = found[at]
            span = tuple(notes[index]) if index is not None else (None, None)
            row.append(Placement(span=span, notes=(index,) if index is not None else ()))
            at += 1
        out.append(row)
    return out


def _share(flat: Sequence[tuple], note: tuple[float, float], index: int) -> float:
    """A mora's share of a note, its end free to reach the next mora's start but not past the note."""
    start, end = flat[index][2], flat[index][3]
    low, high = note
    if start is None or end is None or high <= low:
        return 0.0
    left = max(start, low)
    following = flat[index + 1][2] if index + 1 < len(flat) else None
    right = high if following is None else min(following, high)
    return (max(right, left) - left) / (high - low)


def _groupable(text: str, times, flat: Sequence[tuple], holders: Sequence[int]) -> bool:
    """Whether a note's sharers may be folded into one word, by the same rules a group obeys."""
    if not text:
        return False
    rows = {flat[index][0] for index in holders}
    if len(rows) != 1:
        return False
    columns = sorted(flat[index][1] for index in holders)
    if len(columns) < 2 or columns != list(range(columns[0], columns[-1] + 1)):
        return False
    try:
        group_morae(text, times, rows.pop(), columns[0], columns[-1])
    except KrcError:
        return False
    return True


def _zero_point(start: float | None, notes: Sequence[tuple[float, float]]) -> float | None:
    """Where a mora of no length is drawn: its own onset, else the nearest note's."""
    if start is not None:
        return start
    return notes[0][0] if notes else None
