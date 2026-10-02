# SPDX-License-Identifier: AGPL-3.0-only
"""The global DP that lays the whole song's Sounds onto the whole target NOTE stream at once.

Every Sound is exactly one `match`, `merge` or `drop`, and every note is consumed exactly once, so
the cost - each operation's raw onset against where it predicts the onset lands, plus the line's last
Sound against the line's raw end - is settled over the whole song rather than line by line. A line
boundary does not cut the note stream, so a line's last Sound may run over the rest between lines;
only a `merge` may not cross lines. Confirmed operations are hard anchors: the DP solves the stretches
between them and never moves them.

Exact ties are broken the way the spec orders them: less structural complexity first, then the
solution that keeps the earlier Sound's `match`, then `match` over `merge` over `drop`. The tie is
encoded as a base-3 number over the sounds, one digit per Sound, so a whole song's worth of ties still
compares as an integer; that makes the DP `O(sounds * notes)` states and `O(container length^2)` for
one note's merge candidates, which is the deliberate ceiling here and is worth a per-segment split or
an incremental merge cost only if a real song outgrows it. Qt-free.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from namioto.karaoke.operations import Drop, Match, Merge, Operation, SoundRef
from namioto.karaoke.sounds import SoundLine
from namioto.lyricmap.notes import Note
from namioto.lyricmap.problems import MappingError
from namioto.lyricmap.raw import Raw, chain, validate

# match, merge, drop in the order a tie prefers them
_RANK = {"match": 0, "merge": 1, "drop": 2}


@dataclass(frozen=True)
class _Context:
    info: list[tuple[int, int, tuple]]  # per sound: line, index in line, container key
    line_start: list[int]
    line_count: list[int]
    raw_start: list[float]
    line_end: list[float]
    is_last: list[bool]
    maxm: list[int]
    notes: list
    total: int


def solve(
    lines: Sequence[SoundLine],
    raw: Sequence[Sequence[Raw]],
    notes: Sequence[Note],
    anchors: Sequence[Operation] = (),
) -> list[Operation]:
    """The mapping for the whole song: a partition of its Sounds and the note stream.

    `raw` is one span per Sound, per line, already the aligner's evidence (an onset chain is applied
    here). `notes` is the target channel's single voice, in stream order. `anchors` are the confirmed
    operations the DP must keep. Raises `MappingError` when the input cannot yield a mapping.
    """
    rows = chain(raw)
    validate(lines, rows)
    note_list = list(notes)
    context = _context(lines, rows, note_list)
    if context.total == 0:
        raise MappingError("no_lyric_sounds", "the lyrics have no sounds")
    if not note_list:
        raise MappingError("no_target_notes", "the target channel has no notes")

    pinned, forced = _anchors(anchors, context)
    fixed = set(forced)
    operations: list[Operation] = []
    sound_at, note_at = 0, 0
    for low, high, first, last, operation in pinned:
        operations.extend(_segment(context, sound_at, low, note_at, first, fixed))
        operations.append(operation)
        sound_at, note_at = high, last
    operations.extend(_segment(context, sound_at, context.total, note_at, len(note_list), fixed))
    operations.sort(key=lambda operation: min(context.line_start[ref.line] + ref.index for ref in operation.sounds))
    return [forced.get(_position(operation, context), operation) for operation in operations]


def _position(operation: Operation, context: _Context) -> int | None:
    if not isinstance(operation, Drop):
        return None
    return context.line_start[operation.sound.line] + operation.sound.index


def _context(lines: Sequence[SoundLine], rows: Sequence[Sequence[Raw]], notes: list) -> _Context:
    info: list[tuple[int, int, tuple]] = []
    line_start: list[int] = []
    line_count: list[int] = []
    raw_start: list[float] = []
    line_end: list[float] = []
    for line_index, line in enumerate(lines):
        line_start.append(len(info))
        line_count.append(len(line.sounds))
        line_end.append(rows[line_index][-1].end if line.sounds else 0.0)
        for index, sound in enumerate(line.sounds):
            info.append((line_index, index, (line_index, sound.container)))
            raw_start.append(rows[line_index][index].start)
    total = len(info)
    is_last = [index == line_start[info[index][0]] + line_count[info[index][0]] - 1 for index in range(total)]
    maxm = [1] * total
    for index in range(total - 2, -1, -1):
        if info[index][2] == info[index + 1][2]:
            maxm[index] = maxm[index + 1] + 1
    return _Context(info, line_start, line_count, raw_start, line_end, is_last, maxm, notes, total)


def _boundary(context: _Context, note_at: int) -> float:
    """Where an operation with no note of its own lands: the next note start, else the last end."""
    if note_at < len(context.notes):
        return context.notes[note_at].start
    return context.notes[-1].end


def _line_end(context: _Context, index: int) -> float:
    return context.line_end[context.info[index][0]]


def _drop_base(context: _Context, index: int, note_at: int) -> float:
    point = _boundary(context, note_at)
    cost = abs(context.raw_start[index] - point)
    if context.is_last[index]:
        cost += abs(_line_end(context, index) - point)
    return cost


def _match_base(context: _Context, index: int, note_at: int, count: int) -> float:
    cost = abs(context.raw_start[index] - context.notes[note_at].start)
    if context.is_last[index]:
        cost += abs(_line_end(context, index) - context.notes[note_at + count - 1].end)
    return cost


def _merge_base(context: _Context, index: int, run: int, note_at: int) -> float:
    start, end = context.notes[note_at].start, context.notes[note_at].end
    cost = sum(abs(context.raw_start[index + p] - (start + p * (end - start) / run)) for p in range(run))
    last = index + run - 1
    if context.is_last[last]:
        cost += abs(_line_end(context, last) - end)
    return cost


def _global(ref: SoundRef, context: _Context) -> int:
    if not 0 <= ref.line < len(context.line_start):
        raise MappingError("invalid_anchor", f"the mapping names line {ref.line}")
    found = context.line_start[ref.line] + ref.index
    if not 0 <= ref.index < context.line_count[ref.line]:
        raise MappingError("invalid_anchor", f"the mapping names sound {ref}")
    return found


def _anchors(anchors: Sequence[Operation], context: _Context) -> tuple[list[tuple], dict[int, Operation]]:
    """The confirmed operations split into note-pinning ones and forced drops.

    A `match`/`merge` pins both the Sounds and the notes, so it splits the song; a `drop` pins only
    its Sound and is forced inside the stretch it falls in. Anything inconsistent is `invalid_anchor`.
    """
    pinned: list[tuple] = []
    forced: dict[int, Operation] = {}
    for operation in anchors:
        refs = operation.sounds
        sounds = [_global(ref, context) for ref in refs]
        if not isinstance(operation, Drop):
            if sounds != list(range(min(sounds), min(sounds) + len(sounds))):
                raise MappingError("invalid_anchor", "a confirmed operation's sounds do not run on")
            if isinstance(operation, Merge) and len({context.info[g][2] for g in sounds}) != 1:
                raise MappingError("invalid_anchor", "a confirmed merge crosses a container")
        if isinstance(operation, Match):
            notes = _note_indices(operation.notes, context)
            pinned.append((sounds[0], sounds[0] + 1, notes[0], notes[-1] + 1, operation))
        elif isinstance(operation, Merge):
            notes = _note_indices((operation.note,), context)
            pinned.append((sounds[0], sounds[-1] + 1, notes[0], notes[0] + 1, operation))
        else:
            forced[sounds[0]] = operation
    pinned.sort(key=lambda entry: entry[0])
    sound_at, note_at = 0, 0
    for low, high, first, last, _operation in pinned:
        if low < sound_at or first < note_at or last < first:
            raise MappingError("invalid_anchor", "confirmed operations cross or repeat")
        if any(low <= g < high for g in forced):
            raise MappingError("invalid_anchor", "a confirmed drop falls inside another anchor")
        sound_at, note_at = high, last
    return pinned, forced


def _note_indices(ids: Sequence[int], context: _Context) -> list[int]:
    at = {note.id: index for index, note in enumerate(context.notes)}
    found = [at.get(identifier) for identifier in ids]
    if any(index is None for index in found):
        raise MappingError("invalid_anchor", "a confirmed operation names a note that is not in the stream")
    return found


def _segment(context: _Context, sa: int, sb: int, na: int, nb: int, forced: set[int]) -> list[Operation]:
    """The cheapest mapping of sounds `[sa, sb)` onto notes `[na, nb)`, with forced drops applied."""
    size = sb - sa
    count = nb - na
    if size == 0:
        if count == 0:
            return []
        raise MappingError("invalid_anchor", "notes are left with no sounds to take them")
    weight = [3 ** (size - 1 - step) for step in range(size)]
    next_forced = [size] * (size + 1)
    following = size
    for step in range(size - 1, -1, -1):
        if sa + step in forced:
            following = step
        next_forced[step] = following

    best: list[list[tuple[float, int, int] | None]] = [[None] * (count + 1) for _ in range(size + 1)]
    back: list[list[tuple | None]] = [[None] * (count + 1) for _ in range(size + 1)]
    best[0][0] = (0.0, 0, 0)
    for step in range(size):
        index = sa + step
        forced_here = index in forced
        for have in range(count + 1):
            here = best[step][have]
            if here is None:
                continue
            _relax(
                best,
                back,
                step + 1,
                have,
                _add(here, _drop_base(context, index, na + have), 1, 2 * weight[step]),
                ("d",),
            )
            if forced_here:
                continue
            if have < count:
                top = min(context.maxm[index], next_forced[step] - step)
                for run in range(2, top + 1):
                    _relax(
                        best,
                        back,
                        step + run,
                        have + 1,
                        _add(
                            here, _merge_base(context, index, run, na + have), run - 1, sum(weight[step : step + run])
                        ),
                        ("g", run),
                    )
        if not forced_here:
            _match_run(context, best, back, step, index, na, count, weight)
    found = best[size][count]
    if found is None:
        raise MappingError("invalid_anchor", "the sounds and the notes cannot be partitioned")
    return _rebuild(context, back, sa, na, size, count)


def _match_run(context, best, back, step, index, na, count, weight) -> None:
    """Relax every match from the running cheapest earlier note, in one pass over the notes."""
    run: tuple[float, int, int, int] | None = None
    notes = context.notes
    for have in range(1, count + 1):
        before = best[step][have - 1]
        if before is not None:
            candidate = (
                before[0] + abs(context.raw_start[index] - notes[na + have - 1].start),
                before[1] - (have - 1),
                before[2],
                have - 1,
            )
            if run is None or candidate[:3] < run[:3]:
                run = candidate
        if run is None:
            continue
        end_time = notes[na + have - 1].end
        base = run[0] + (abs(_line_end(context, index) - end_time) if context.is_last[index] else 0.0)
        _relax(best, back, step + 1, have, (base, run[1] + (have - 1), run[2]), ("m", run[3]))


def _add(value: tuple[float, int, int], base: float, complexity: int, rank: int) -> tuple[float, int, int]:
    return (value[0] + base, value[1] + complexity, value[2] + rank)


def _relax(best, back, row: int, column: int, value: tuple[float, int, int], pointer: tuple) -> None:
    here = best[row][column]
    if here is None or value < here:
        best[row][column] = value
        back[row][column] = pointer


def _rebuild(context: _Context, back, sa: int, na: int, size: int, count: int) -> list[Operation]:
    operations: list[Operation] = []
    step, have = size, count
    while step or have:
        pointer = back[step][have]
        if pointer is None:
            raise MappingError("invalid_anchor", "the mapping has no complete partition")
        index = sa + step - 1
        line, position, _container = context.info[index]
        if pointer[0] == "d":
            operations.append(Drop(SoundRef(line, position)))
            step -= 1
        elif pointer[0] == "m":
            first = pointer[1]
            ids = tuple(context.notes[na + at].id for at in range(first, have))
            operations.append(Match(SoundRef(line, position), ids))
            step, have = step - 1, first
        else:
            run = pointer[1]
            first = sa + step - run
            note = context.notes[na + have - 1].id
            refs = tuple(SoundRef(context.info[at][0], context.info[at][1]) for at in range(first, first + run))
            operations.append(Merge(refs, note))
            step, have = step - run, have - 1
    operations.reverse()
    return operations


def backward(context: _Context) -> list[list[float]]:
    """`grid[i][j]`: the cheapest base cost from state (i, j) - i Sounds and j notes taken - to the end.

    A match's cost does not depend on how many notes it takes when the Sound is not its line's last,
    so its best length is a suffix minimum over the row; a line-last Sound folds its raw end into the
    same suffix. That is what keeps the backward pass at the same `O(sounds * notes)` as the forward.
    """
    total = context.total
    notes = context.notes
    size = len(notes)
    infinity = float("inf")
    grid = [[infinity] * (size + 1) for _ in range(total + 1)]
    grid[total][size] = 0.0
    for index in range(total - 1, -1, -1):
        row = grid[index]
        nxt = grid[index + 1]
        row[size] = _drop_base(context, index, size) + nxt[size]
        if context.is_last[index]:
            line_end = _line_end(context, index)
            suffix = [infinity] * (size + 1)
            for end_at in range(size - 1, -1, -1):
                suffix[end_at] = min(abs(line_end - notes[end_at].end) + nxt[end_at + 1], suffix[end_at + 1])
        else:
            suffix = [infinity] * (size + 1)
            for end_at in range(size - 1, -1, -1):
                suffix[end_at] = min(nxt[end_at + 1], suffix[end_at + 1])
        onset = context.raw_start[index]
        top = min(context.maxm[index], total - index)
        for at in range(size - 1, -1, -1):
            best = _drop_base(context, index, at) + nxt[at]
            match = abs(onset - notes[at].start) + suffix[at]
            if match < best:
                best = match
            for run in range(2, top + 1):
                candidate = _merge_base(context, index, run, at) + grid[index + run][at + 1]
                if candidate < best:
                    best = candidate
            row[at] = best
    return grid


def diagnose(
    lines: Sequence[SoundLine], raw: Sequence[Sequence[Raw]], notes: Sequence[Note], operations: Sequence[Operation]
) -> list[tuple[float, float]]:
    """Per operation, its own fit error and its margin over the best alternative from the same state.

    The margin keeps the operation's prefix and asks what the cheapest mapping from there without it
    would cost; the completion is the unanchored backward pass, so with confirmed anchors the margin
    of the suggested operations around them is approximate. Both numbers are in seconds; normalising
    and judging them belongs to `confidence`.
    """
    rows = chain(raw)
    validate(lines, rows)
    note_list = list(notes)
    if not note_list:
        return []
    context = _context(lines, rows, note_list)
    grid = backward(context)
    found: list[tuple[float, float]] = []
    sound_at, note_at = 0, 0
    for operation in operations:
        if isinstance(operation, Match):
            count = len(operation.notes)
            base = _match_base(context, sound_at, note_at, count)
            landing = (sound_at + 1, note_at + count)
        elif isinstance(operation, Merge):
            count = len(operation.sounds)
            base = _merge_base(context, sound_at, count, note_at)
            landing = (sound_at + count, note_at + 1)
        else:
            base = _drop_base(context, sound_at, note_at)
            landing = (sound_at + 1, note_at)
        alternative = _alternative(context, grid, sound_at, note_at, operation)
        found.append((base, alternative - (base + grid[landing[0]][landing[1]])))
        sound_at, note_at = landing
    return found


def _alternative(context: _Context, grid, sound_at: int, note_at: int, operation: Operation) -> float:
    """The cheapest mapping from this state that does not take this operation."""
    best = float("inf")
    if not isinstance(operation, Drop):
        best = _drop_base(context, sound_at, note_at) + grid[sound_at + 1][note_at]
    if note_at < len(context.notes):
        for count in range(1, len(context.notes) - note_at + 1):
            if isinstance(operation, Match) and count == len(operation.notes):
                continue
            best = min(best, _match_base(context, sound_at, note_at, count) + grid[sound_at + 1][note_at + count])
        top = min(context.maxm[sound_at], context.total - sound_at)
        for run in range(2, top + 1):
            if isinstance(operation, Merge) and run == len(operation.sounds):
                continue
            best = min(best, _merge_base(context, sound_at, run, note_at) + grid[sound_at + run][note_at + 1])
    return best
