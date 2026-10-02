# SPDX-License-Identifier: AGPL-3.0-only
"""The single voice the lyrics may map to: the target channel's notes, and the one path through them.

A lyric mapping needs one NOTE per instant. Where the target channel's notes overlap - any pitch
overlapping any other - the notes that stay are chosen as a maximal non-overlapping path through each
connected region of the overlap graph, and the rest are *filtered*: they are a blocking error the user
resolves by fixing the MIDI, never by confirming them away. Which path is chosen is the mapping's own
cost when it is given one; among equal costs the path that keeps more notes wins, then the one whose
note ids read smaller. Qt-free.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass
from typing import Protocol

from namioto.document import OVERLAP_SLACK


class Note(Protocol):
    """What the stream reads of a note: its times, its pitch and its stable id.

    `document.Note` and `project.Note` both satisfy it; only `document.Note` has an `end` property,
    which the callers here pass as beats.
    """

    start: float
    end: float
    pitch: int
    id: int


@dataclass(frozen=True)
class Branch:
    """One candidate path through a conflict region, and what the mapping makes of it."""

    notes: tuple
    cost: float = 0.0


@dataclass(frozen=True)
class Resolved:
    """The target channel as a single voice: the notes that stay, and the ones filtered out.

    `branches` holds, per conflict region, every maximal path the UI may offer as a preview branch.
    """

    stream: tuple
    filtered: tuple
    branches: tuple[tuple[Branch, ...], ...] = ()


def ordered(notes: Iterable[Note]) -> list[Note]:
    """The notes in their one deterministic order: start, end, pitch, persistent id."""
    return sorted(notes, key=lambda note: (note.start, note.end, note.pitch, note.id))


def overlaps(one: Note, other: Note) -> bool:
    """Whether two notes are one instant: their spans cross by more than `OVERLAP_SLACK`, any pitch.

    Only touching or overlapping by no more than the slack is legal.
    """
    return one.start < other.end - OVERLAP_SLACK and other.start < one.end - OVERLAP_SLACK


def components(notes: Iterable[Note]) -> list[list[Note]]:
    """The connected regions of the overlap graph, each in `ordered` order.

    The notes are swept in start order: a note whose start is under the running end of the region
    overlaps the note that end belongs to, so it joins; any later start opens a new region, because
    every note before it ends no later than the running end.
    """
    regions: list[list[Note]] = []
    current: list[Note] = []
    reach = float("-inf")
    for note in ordered(notes):
        if current and note.start < reach - OVERLAP_SLACK:
            current.append(note)
            reach = max(reach, note.end)
            continue
        if current:
            regions.append(current)
        current = [note]
        reach = note.end
    if current:
        regions.append(current)
    return regions


def resolve(notes: Iterable[Note], cost: Callable[[tuple], float] | None = None) -> Resolved:
    """Reduce the notes to one voice, choosing each region's path by `cost` and its tie-breaks.

    `cost(path)` is the mapping's own time cost for a candidate path; a caller that only wants a
    preview may leave it out, and the paths then tie, so the one keeping more notes and the smaller
    ids wins.
    """
    regions = components(notes)
    kept: list[Note] = []
    filtered: list[Note] = []
    branches: list[tuple[Branch, ...]] = []
    for region in regions:
        if len(region) == 1:
            kept.append(region[0])
            continue
        paths = list(_maximal_paths(region))
        if not paths:
            continue
        priced = [Branch(tuple(path), 0.0 if cost is None else cost(tuple(path))) for path in paths]
        chosen = _best(priced)
        branches.append(tuple(priced))
        kept.extend(chosen.notes)
        filtered.extend(note for note in region if note not in chosen.notes)
    return Resolved(tuple(kept), tuple(filtered), tuple(branches))


def _best(branches: Sequence[Branch]) -> Branch:
    """The path with the smallest cost, then the most notes, then the smallest note ids."""
    return min(branches, key=lambda branch: (branch.cost, -len(branch.notes), tuple(_ids(branch.notes))))


def _ids(notes: Sequence[Note]) -> list[int]:
    return [note.id for note in notes]


def _maximal_paths(region: list[Note]) -> Iterator[tuple[Note, ...]]:
    """Every maximal set of pairwise non-overlapping notes in one conflict region.

    The region is small - a handful of notes crossing in time - so the search branches on each note
    in turn; a branch that leaves a note addable is not maximal and is dropped at the leaf.
    """
    seen: set[tuple[int, ...]] = set()

    def leaf(chosen: list[Note]) -> bool:
        return all(note in chosen or any(overlaps(note, other) for other in chosen) for note in region)

    def walk(available: list[Note], chosen: list[Note]) -> Iterator[tuple[Note, ...]]:
        if not available:
            if leaf(chosen):
                key = tuple(_ids(chosen))
                if key not in seen:
                    seen.add(key)
                    yield tuple(chosen)
            return
        first, rest = available[0], available[1:]
        free = [note for note in rest if not overlaps(note, first)]
        yield from walk(free, [*chosen, first])
        if any(overlaps(note, first) for note in rest):
            yield from walk(rest, chosen)

    yield from walk(list(region), [])
