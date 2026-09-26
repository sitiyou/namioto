# SPDX-License-Identifier: AGPL-3.0-only
"""The `.krc` as a timeline: a `Sound` per mora of every `Unit`, each carrying the one token the
aligner reads.

`sound_lines` flattens the parsed lyrics into rows of sounds for the strip - one per mora a unit
reads - and `align_tokens` joins them into the single token stream `namioto.analysis.align` reads. A
token here is a per-sound refinement of `namioto.utils.kana_tokens`: the characters are the same and
in the same order, so the forced alignment is unchanged, but a long vowel or a sokuon gets a token of
its own and so a time of its own. A kanji the `.krc` never gave a ruby raises, because there is no
sound to align it to.

`split` folds the aligner's flat token stream back onto the lines, `note_counts`/`conflicts` judge
the times against the notes, and `with_counts` writes each word's mora back out as `.N`.

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
OWN_SOUND = frozenset("ーっッ")
# how far a sound's time may sit from the note it should start and end on, one aligner frame of slack
TOLERANCE = 0.05
# the least of a shared note a sound must keep for the note to stay its own; below it the sound loses it
SHARE = 0.25


@dataclass(frozen=True)
class Sound:
    """One sound: the surface it belongs to, the kana it reads, and the token the aligner reads.

    A sound of a ruby-bearing word carries the base unit it is read from - the whole run of kanji
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
class SoundLine:
    """One lyric line as its sounds, in order."""

    text: str
    sounds: tuple[Sound, ...]


def sound_lines(text: str) -> list[SoundLine]:
    """Every line of a `.krc`, as its sounds.

    A kanji without a ruby, or a long vowel or sokuon with no sound to lean on, raises `KrcError`.
    """
    return [_row(line)[0] for chapter in parse(text).chapters for line in chapter.lines]


def align_tokens(lines: list[SoundLine]) -> list[str]:
    """The token stream the aligner reads: every line's sounds, in order, one token each."""
    return [sound.token for line in lines for sound in line.sounds]


def text_key(text: str) -> str:
    """A short key for a `.krc` text, so the times stored beside it can tell when it changed."""
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def split(tokens: Sequence, lines: list[SoundLine]) -> list[list[tuple[float | None, float | None]]]:
    """The aligner's flat tokens, one per sound in order, cut back into the lines' rows."""
    rows = []
    at = 0
    for line in lines:
        row = tokens[at : at + len(line.sounds)]
        rows.append([(token.start, token.end) for token in row])
        at += len(row)
    return rows


def snap_to_beats(
    times: list[list[tuple[float | None, float | None]]], bpm: float, division: float = 1.0, offset: float = 0.0
) -> list[list[tuple[float | None, float | None]]]:
    """Every sound's start and end rounded to the grid of `division` beats at `bpm` off `offset`.

    The grid is the one that is drawn: `offset` is the editor's slid grid, 0 the absolute beats. A
    sound rounds on its own, so one whose two ends land in the same cell comes back with no length -
    a sound nothing is sung on, which the strip and the conflict check already read - rather than
    pushing the rest of its line one cell per collision off the beat. A line with an unaligned sound
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
    lines: list[SoundLine], times: list[list[tuple[float | None, float | None]]], notes: Sequence[tuple[float, float]]
) -> list[list[int]]:
    """How many notes each sound covers: the notes wholly inside its span, gaps allowed."""
    return [[_count(span, notes) for span in row] for row in times]


def contiguous(
    times: Sequence[Sequence[tuple[float | None, float | None]]],
) -> list[list[tuple[float | None, float | None]]]:
    """Every sound's end taken from the next sound's start: the aligner's own end is dropped.

    The strip reads a line as a chain of onsets - each `|` marks where a sound begins and the sound
    ends where the next begins - so only the starts carry the timing and a sound's own end says
    nothing. The last sound of a line keeps the end it came with, since no `|` follows it.
    """
    rows = []
    for row in times:
        spans = [list(span) for span in row]
        for index in range(len(spans) - 1):
            if spans[index][0] is not None and spans[index + 1][0] is not None:
                spans[index][1] = spans[index + 1][0]
        rows.append([tuple(span) for span in spans])
    return rows


def conflicts(
    lines: list[SoundLine], times: list[list[tuple[float | None, float | None]]], notes: Sequence[tuple[float, float]]
) -> list[str]:
    """Why the text and the notes do not line up yet: an unaligned, unbacked or shared sound."""
    problems = []
    claimed: dict[int, str] = {}
    for line, row in zip(lines, times, strict=True):
        if len(row) != len(line.sounds):
            problems.append(f"'{line.text}' has {len(line.sounds)} sounds but {len(row)} times")
            continue
        for sound, (start, end) in zip(line.sounds, row, strict=True):
            label = sound.label
            if start is None or end is None:
                problems.append(f"{label}: not aligned")
                continue
            if end <= start:
                continue  # a sound of no length has nothing to sit on
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


def sound_ok(
    lines: list[SoundLine], times: list[list[tuple[float | None, float | None]]], notes: Sequence[tuple[float, float]]
) -> list[list[bool]]:
    """Whether each sound sits on its own notes, the same judgement `conflicts` reports by hand.

    A sound is not settled when it is unaligned, backs no note, does not start and end on its
    boundary notes, or shares a note with another sound.
    """
    ok = []
    holders: dict[int, list[tuple[int, int]]] = {}
    for line, row in zip(lines, times, strict=True):
        flags = []
        for column, (start, end) in enumerate(row):
            if start is not None and end is not None and end <= start:
                flags.append(len(row) == len(line.sounds))  # a sound of no length has nothing to sit on
                continue
            inside = (
                [index for index, note in enumerate(notes) if _inside(note, start, end)]
                if start is not None and end is not None
                else []
            )
            good = bool(inside) and len(row) == len(line.sounds)
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
    """A `.krc` with each word's `.N` set to the mora it reads, from its sound's note count.

    `.N` is `Unit.override`, the unit's mora count; under `sum(unit.mora) == #NOTE` a sound covering
    N notes makes its word read N morae. A `.N` already equal to the reading is left off.
    """
    lyrics = parse(text)
    index = 0
    for chapter in lyrics.chapters:
        for line in chapter.lines:
            _line, sources, _locations = _row(line)
            for source, count in zip(sources, counts[index], strict=True):
                source.override = None if count == source.natural_mora else count
            index += 1
    return dumps(lyrics)


def group_sounds(text: str, row: int, first: int, last: int) -> str:
    """Fold the sounds `first..last` of one line into one word of the `.krc`, written `(...)`.

    The run must sit in one container - the top-level words or one ruby part - since the `.krc` has
    no boundary to make a word out of a run that crosses a ruby part or a ruby word and a plain one
    (see the spec's conversion section). The format has no nested parentheses, so a run that starts
    or ends inside an existing `(...)` dissolves that group and re-forms it. The kana is read the
    same way, so the aligner's token stream does not move - a run that would move a sound raises
    `KrcError`.
    """
    lyrics = parse(text)
    lines = [line for chapter in lyrics.chapters for line in chapter.lines]
    if not 0 <= row < len(lines):
        raise KrcError(f"the lyrics have no line {row}")
    line = lines[row]
    _line, _sources, loc = _row(line, True)
    if not 0 <= first < last < len(loc):
        raise KrcError("a group is made of two sounds or more of one line")
    run = loc[first : last + 1]
    ruby = {item[2] for item in run}
    if ruby == {None}:
        _dissolve_top(line, loc[first][0], loc[last][1])
    elif len(ruby) == 1 and len({item[0] for item in run}) == 1:
        _dissolve_part(line, run[0][0], run[0][2], loc[first][3], loc[last][5])
    else:
        raise KrcError("a group cannot cross a ruby part or a word")

    _line, _sources, loc = _row(line, True)
    if ruby == {None}:
        low, high = loc[first][0], loc[last][1]
        chars = "".join(unit.text for unit in line.units[low : high + 1])
        line.units = [*line.units[:low], Unit(Group([Word(char) for char in chars])), *line.units[high + 1 :]]
    else:
        low, high = loc[first][3], loc[last][5]
        part_units = line.units[run[0][0]].ruby.parts[run[0][2]]
        chars = "".join(unit.text for unit in part_units[low : high + 1])
        part_units[low : high + 1] = [Unit(Group([Word(char) for char in chars]))]

    grouped = dumps(lyrics)
    if _flatten_tokens(grouped) != _flatten_tokens(text):
        raise KrcError("folding these sounds into one word would move a sound")
    return grouped


def _flatten_tokens(text: str) -> list[list[str]]:
    """The editor's token stream: one token per sound, as `sound_lines` reads it back."""
    return [[sound.token for sound in line.sounds] for line in sound_lines(text)]


def _dissolve_top(line: Line, low: int, high: int) -> None:
    """Break the `(...)` words in `line.units[low:high+1]` into their members, so the run has edges."""
    units: list[Unit] = []
    for index, unit in enumerate(line.units):
        if low <= index <= high and _is_plain_group(unit):
            units.extend(Unit(word) for word in unit.base.words)
        else:
            units.append(unit)
    line.units = units


def _dissolve_part(line: Line, top: int, part: int, low: int, high: int) -> None:
    """Break the `(...)` words in one ruby part between the two sounds into their members."""
    part_units = line.units[top].ruby.parts[part]
    units: list[Unit] = []
    for index, inner in enumerate(part_units):
        if low <= index <= high and _is_plain_group(inner):
            units.extend(Unit(word) for word in inner.base.words)
        else:
            units.append(inner)
    line.units[top].ruby.parts[part] = units


def _is_plain_group(unit: Unit) -> bool:
    return unit.ruby is None and isinstance(unit.base, Group) and len(unit.base.words) > 1 and not unit.is_latin()


def _inside(note: tuple[float, float], start: float, end: float) -> bool:
    return note[0] >= start - TOLERANCE and note[1] <= end + TOLERANCE


def _count(span: tuple[float | None, float | None], notes: Sequence[tuple[float, float]]) -> int:
    start, end = span
    if start is None or end is None:
        return 0
    return sum(1 for note in notes if _inside(note, start, end))


def _row(line: Line, split_groups: bool = True) -> tuple[SoundLine, list[Word], list[tuple]]:
    """The line as its sounds, their source words, and where each sound sits in the parsed line.

    `split_groups` reads a `(...)` word back as its members (the editor's view); off, it stays one
    unit, which is what `group_sounds` folds and measures. A location is
    `(top_first, top_last, part, inner_first, member_first, inner_last, member_last)`: the top-level
    words the sound spans (a small kana widens the range), the ruby part it sits in (`None` at the
    top level), and the word it covers there.
    """
    text = "".join(word.text for word in line.words)
    unread = [word.text for word in line.words if word.ruby is None and word.is_kanji()]
    if unread:
        raise KrcError(f"'{text}' has kanji with no ruby to align: {' '.join(unread)}")

    units: list[list] = []
    sources: list[Word] = []
    locations: list[tuple] = []
    for top, word in enumerate(line.words):
        for base, kana, rubied, first, source, part, inner, member in _units(word, split_groups):
            if kana in SMALL_KANA:
                if units:
                    units[-1][1] += kana
                    if not units[-1][2]:
                        # a plain surface takes the small kana too, so its block reads ショ, not シ
                        units[-1][0] += kana
                    old = locations[-1]
                    if part is None:
                        locations[-1] = (*old[:1], top, *old[2:])
                    else:
                        locations[-1] = (*old[:5], inner, old[6])
                continue
            if word.natural_mora == 0:
                continue
            units.append([base, kana, rubied, first])
            sources.append(source)
            locations.append((top, top, part, inner, member, inner, member))

    folded = [char for token in kana_tokens("".join(kana for _base, kana, _rubied, _first in units)) for char in token]
    sizes = [_size(kana) for _base, kana, _rubied, _first in units]
    if sum(sizes) != len(folded):
        raise KrcError(f"'{text}' has a long vowel or a sokuon with no sound to lean on")

    sounds = []
    at = 0
    for (base, kana, rubied, first), size in zip(units, sizes, strict=True):
        sounds.append(Sound(base, kana, "".join(folded[at : at + size]), rubied, first))
        at += size
    return SoundLine(text, tuple(sounds)), sources, locations


def _units(word: Unit, split_groups: bool = True) -> list[tuple]:
    """`(surface, kana, rubied, first, source, part, inner, member)` per sound.

    With `split_groups`, a `(...)` word is read back as its members, so `(しょう)` gives `しょ` and
    `う` again - the small kana riding the one before, exactly as the ungrouped run would. The
    members of a group inside a ruby all carry that ruby's surface, so `胡椒[こ,(しょう)]` reads
    `こ(胡)`, `しょ[椒]`, `う[椒]` - the same sounds and token stream as `胡椒[こ,しょう]`.

    `part`/`inner` are the ruby part the sound reads in (`None` at the top level); `member` is the
    word it is inside a `(...)` group (`None` when the sound is a whole word).
    """
    if word.ruby is None:
        if split_groups and isinstance(word.base, Group) and len(word.base.words) > 1 and not word.is_latin():
            return [
                (inner.text, inner.text, False, True, inner, None, None, member)
                for member, inner in enumerate(word.base.words)
            ]
        return [(word.text, word.text, False, True, word, None, None, None)]
    one_part = len(word.ruby.parts) == 1
    units = []
    for part_index, part in enumerate(word.ruby.parts):
        surface = word.text if one_part else word.text[part_index : part_index + 1]
        for inner_index, inner in enumerate(part):
            if split_groups and isinstance(inner.base, Group) and len(inner.base.words) > 1 and not inner.is_latin():
                units.extend(
                    (surface, member.text, True, part_index == 0, member, part_index, inner_index, m)
                    for m, member in enumerate(inner.base.words)
                )
            else:
                units.append((surface, inner.text, True, part_index == 0, inner, part_index, inner_index, None))
    return units


def _size(kana: str) -> int:
    """How many characters this sound owns in the folded stream; a long vowel or sokuon owns one."""
    if kana in OWN_SOUND:
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
    """Where one sound sits once the notes are read: its span, the notes it covers, and its doubt.

    `span` is what the strip draws - the note(s) it covers, a group's slice of a shared note, or a
    point for a sound of no length. `notes` are the note indices it covers, `zero` a sound that fell
    on none, and `red` one whose time the aligner cannot be trusted for.
    """

    span: tuple[float | None, float | None]
    notes: tuple[int, ...] = ()
    zero: bool = False
    red: bool = False
    group: int = -1  # the note this sound shares with its neighbours, or -1 on its own


def map_faithful(text: str, notes: Sequence[tuple[float, float]]) -> list[list[Placement]]:
    """The `.krc` laid on the notes with nothing estimated: a unit's mora count takes the next notes.

    The `.krc` carries no times, so the notes are the times: a unit of `mora` notes takes that many
    in reading order, and everything stops when the notes run out. Its sounds share those notes - a
    unit with more notes than sounds holds one over several (`あ.2`), one with fewer puts several on
    a note (`(あい).1`), which shows as a group. This is a reading of the file, not an edit of it.
    """
    found: list[list[Placement]] = []
    at = 0
    for chapter in parse(text).chapters:
        for line in chapter.lines:
            _line, _sources, loc = _row(line, True)
            row: list[Placement] = []
            for top, unit in enumerate(line.units):
                if not unit.natural_mora:
                    continue
                sounds = sum(1 for item in loc if item[0] == top)
                taken = notes[at : at + unit.mora]
                row.extend(_faithful_unit(sounds, taken, at))
                at += len(taken)
            found.append(row)
    return found


def _faithful_unit(natural: int, taken: Sequence[tuple[float, float]], base: int) -> list[Placement]:
    """`natural` sounds on the `taken` notes (indices from `base`): a held tail or shared notes."""
    if not taken:
        return [Placement(span=(None, None), zero=True) for _ in range(natural)]
    if len(taken) >= natural:
        out = []
        for index in range(natural):
            low = index * len(taken) // natural
            high = (index + 1) * len(taken) // natural
            part = taken[low:high]
            out.append(Placement(span=(part[0][0], part[-1][1]), notes=tuple(range(base + low, base + high))))
        return out
    low, high = taken[0][0], taken[-1][1]
    width = (high - low) / natural
    spans = [(low + index * width, low + (index + 1) * width) for index in range(natural)]
    covered: list[list[int]] = [[] for _ in range(natural)]
    group = [-1] * natural
    for offset, note in enumerate(taken):
        holders = [index for index, (start, end) in enumerate(spans) if start < note[1] and end > note[0]]
        for index in holders:
            covered[index].append(base + offset)
        if len(holders) > 1:
            for index in holders:
                group[index] = base + offset
    return [Placement(span=spans[i], notes=tuple(covered[i]), group=group[i]) for i in range(natural)]


def map_sounds(
    lines: Sequence[SoundLine],
    times: Sequence[Sequence[tuple[float | None, float | None]]],
    notes: Sequence[tuple[float, float]],
    text: str = "",
    flagged: Sequence[bool] | None = None,
    *,
    aligned: bool = True,
) -> list[list[Placement]]:
    """Put every sound on the notes its time covers, and settle the notes its neighbours share.

    With `aligned`, a sound covers every note its time overlaps; a note several sounds share stays the
    own of each that keeps a share of it of at least `SHARE` (a quarter), and the ones left under
    that fall to no length - so a strong sound is never dragged down by a weak neighbour on the same
    note. A note no sound reaches is given to the one before it and doubted, so every note is
    answered for. Without `aligned` the sounds and the notes are paired one for one, in reading
    order, until the shorter side runs out. `flagged` marks the lines the aligner itself doubted,
    which reddens the whole line.
    """
    flat: list[tuple[int, int, float | None, float | None]] = []
    for row, line in enumerate(lines):
        for column, _sound in enumerate(line.sounds):
            present = row < len(times) and column < len(times[row])
            span = times[row][column] if present else (None, None)
            flat.append((row, column, span[0], span[1]))

    if not aligned:
        return _by_order(lines, notes, assign_by_order(len(flat), len(notes)))
    if not flat or not notes:
        return [[Placement((None, None)) for _sound in line.sounds] for line in lines]

    holders: list[list[int]] = [[] for _note in notes]
    for index, (_row, _column, start, end) in enumerate(flat):
        if start is None or end is None or end < start:
            continue
        for note, (low, high) in enumerate(notes):
            if min(end, high) > max(start, low):
                holders[note].append(index)

    owner: list[list[int]] = [[] for _note in notes]
    grouped: dict[int, int] = {}  # sound -> the note its neighbours share it with
    doubted: set[int] = set()
    for note, sharers in enumerate(holders):
        if not sharers:
            continue
        if len(sharers) == 1:
            owner[note] = list(sharers)
            continue
        shares = [_share(flat, notes[note], sound) for sound in sharers]
        kept = [sound for sound, share in zip(sharers, shares, strict=True) if share >= SHARE]
        if not kept:
            kept = [sharers[max(range(len(sharers)), key=lambda at: shares[at])]]
        owner[note] = kept
        if len(kept) > 1:
            for sound in kept:
                grouped.setdefault(sound, note)

    for note in range(len(notes)):
        if owner[note]:
            continue
        before = next((other for other in range(note - 1, -1, -1) if owner[other]), None)
        if before is not None:
            sound = owner[before][-1]
        else:
            after = next((other for other in range(note + 1, len(notes)) if owner[other]), None)
            if after is None:
                continue
            sound = owner[after][0]
        owner[note] = [sound]
        doubted.add(sound)

    covered: dict[int, list[int]] = {}
    for note, owners in enumerate(owner):
        for sound in owners:
            covered.setdefault(sound, []).append(note)

    pieces: dict[int, list[tuple[float, float]]] = {}
    for note, owners in enumerate(owner):
        if not owners:
            continue
        low, high = notes[note]
        if len(owners) == 1:
            pieces.setdefault(owners[0], []).append((low, high))
            continue
        shares = [_share(flat, notes[note], sound) for sound in owners]
        total = sum(shares) or float(len(owners))
        edge = low
        for sound, share in zip(owners, shares, strict=True):
            width = (high - low) * share / total
            pieces.setdefault(sound, []).append((edge, edge + width))
            edge += width

    marked = list(flagged) if flagged is not None else []
    found: list[list[Placement]] = []
    index = 0
    for row, line in enumerate(lines):
        red_line = row < len(marked) and bool(marked[row])
        placements = []
        for _sound in line.sounds:
            notes_of = covered.get(index)
            if notes_of:
                chunks = pieces[index]
                span = (min(chunk[0] for chunk in chunks), max(chunk[1] for chunk in chunks))
                placements.append(
                    Placement(
                        span=span,
                        notes=tuple(notes_of),
                        red=red_line or index in doubted,
                        group=grouped.get(index, -1),
                    )
                )
            else:
                point = _zero_point(flat[index][2], notes)
                placements.append(Placement(span=(point, point), zero=True, red=red_line))
            index += 1
        found.append(placements)
    return found


def _by_order(lines: Sequence[SoundLine], notes: Sequence[tuple[float, float]], found) -> list[list[Placement]]:
    """The sounds and the notes matched in reading order, one for one, with nothing doubted."""
    out: list[list[Placement]] = []
    at = 0
    for line in lines:
        row = []
        for _sound in line.sounds:
            index = found[at]
            span = tuple(notes[index]) if index is not None else (None, None)
            row.append(Placement(span=span, notes=(index,) if index is not None else ()))
            at += 1
        out.append(row)
    return out


def _share(flat: Sequence[tuple], note: tuple[float, float], index: int) -> float:
    """A sound's share of a note, its end free to reach the next sound's start but not past the note."""
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
        group_sounds(text, rows.pop(), columns[0], columns[-1])
    except KrcError:
        return False
    return True


def _zero_point(start: float | None, notes: Sequence[tuple[float, float]]) -> float | None:
    """Where a sound of no length is drawn: its own onset, else the nearest note's."""
    if start is not None:
        return start
    return notes[0][0] if notes else None
