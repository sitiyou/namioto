# SPDX-License-Identifier: AGPL-3.0-only
"""Read-only mode: the `.krc`'s own `.N` and groups lay the Sounds out, with nothing estimated.

This is not the edit mapping and shares none of its state: the input's NOTE slots are read in order,
never solved for. A writable unit takes the `.N` slots it carries (or one per natural Sound), and its
Sounds share those slots - a unit with fewer slots than Sounds spreads several Sounds over one note,
a unit with more holds one Sound over several. A ruby descends part by part and inner unit by inner
unit unless the outer unit carries a `.N` of its own. When the notes run out the rest have no time to
render. Qt-free.
"""

from __future__ import annotations

from collections.abc import Sequence

from namioto.karaoke.model import Line
from namioto.karaoke.parser import parse
from namioto.karaoke.sounds import SoundLine, _chars, natural_sounds
from namioto.lyricmap.notes import Note

Span = tuple[float, float] | None


def read(text: str, notes: Sequence[Note]) -> list[list[Span]]:
    """The input `.krc`'s own NOTE slots, one span per natural Sound, in lines."""
    return lay_out(text, notes)[0]


def lay_out(text: str, notes: Sequence[Note]) -> tuple[list[list[Span]], list[list[tuple[int, ...]]]]:
    """The `.N` spans, and beside them the stable ids of the notes each Sound takes.

    The ids say which notes a Sound landed on where a span alone cannot: a Sound over several notes
    carries several, and Sounds sharing one note each carry that same id.
    """
    lines = natural_sounds(text)
    if not text.strip():
        return [], []
    parsed = [line for chapter in parse(text).chapters for line in chapter.lines]
    rows: list[list[Span]] = []
    found: list[list[tuple[int, ...]]] = []
    at = 0
    for sound_line, line in zip(lines, parsed, strict=True):
        row: list[Span] = [None] * len(sound_line.sounds)
        ids: list[tuple[int, ...]] = [() for _sound in sound_line.sounds]
        for override, indices in _leaves(line, sound_line):
            slots = override if override is not None else len(indices)
            taken = list(notes[at : at + slots])
            at += len(taken)
            _distribute(row, ids, indices, taken, slots)
        rows.append(row)
        found.append(ids)
    return rows, found


def consumes(text: str, notes: Sequence[Note]) -> int:
    """How many notes the faithful reading asks for; more than `len(notes)` leaves Sounds untimed."""
    if not text.strip():
        return 0
    parsed = [line for chapter in parse(text).chapters for line in chapter.lines]
    used = 0
    for sound_line, line in zip(natural_sounds(text), parsed, strict=True):
        for override, indices in _leaves(line, sound_line):
            used += override if override is not None else len(indices)
    return used


def _leaves(line: Line, sound_line: SoundLine) -> list[tuple[int | None, list[int]]]:
    """Each writable unit of one line, with the natural Sound indices whose last character it holds."""
    index_of = {container.key: index for index, container in enumerate(sound_line.containers)}
    by_container: dict[int, list] = {}
    for sound in sound_line.sounds:
        by_container.setdefault(sound.container, []).append(sound)
    leaves: list[tuple[int | None, list[int]]] = []
    runs = 0
    run_units: list[tuple[int, int, object]] = []
    offset = 0

    def flush() -> None:
        nonlocal runs, run_units, offset
        if not run_units:
            return
        container = index_of.get(("top", runs))
        if container is not None:
            for low, high, unit in run_units:
                owned = [sound.index for sound in by_container.get(container, []) if low <= sound.last_atom <= high]
                if owned:
                    leaves.append((unit.override, owned))
        runs += 1
        run_units = []
        offset = 0

    for top, unit in enumerate(line.units):
        if unit.ruby is None:
            chars = _chars(unit)
            run_units.append((offset, offset + len(chars) - 1, unit))
            offset += len(chars)
            continue
        flush()
        if unit.override is not None:
            owned = []
            for part in range(len(unit.ruby.parts)):
                container = index_of.get(("part", top, part))
                if container is not None:
                    owned.extend(sound.index for sound in by_container.get(container, []))
            leaves.append((unit.override, owned))
            continue
        for part_index, part in enumerate(unit.ruby.parts):
            container = index_of.get(("part", top, part_index))
            if container is None:
                continue
            offset = 0
            for inner in part:
                chars = _chars(inner)
                owned = [
                    sound.index
                    for sound in by_container.get(container, [])
                    if offset <= sound.last_atom <= offset + len(chars) - 1
                ]
                if owned:
                    leaves.append((inner.override, owned))
                offset += len(chars)
    flush()
    return leaves


def _distribute(
    row: list[Span], ids: list[tuple[int, ...]], indices: list[int], taken: Sequence[Note], slots: int
) -> None:
    """Lay `slots` notes over the Sounds `indices`, holding, sharing or running out."""
    natural = len(indices)
    if not taken:
        for index in indices:
            row[index] = None
            ids[index] = ()
        return
    if slots >= natural:
        for position, sound_index in enumerate(indices):
            low = position * slots // natural
            high = (position + 1) * slots // natural
            chunk = taken[low:high]
            row[sound_index] = (chunk[0].start, chunk[-1].end) if chunk else None
            ids[sound_index] = tuple(note.id for note in chunk)
        return
    for slot in range(slots):
        low = slot * natural // slots
        high = (slot + 1) * natural // slots
        width = (taken[slot].end - taken[slot].start) / (high - low)
        for position in range(low, high):
            start = taken[slot].start + (position - low) * width
            row[indices[position]] = (start, start + width)
            ids[indices[position]] = (taken[slot].id,)


__all__ = ["Span", "consumes", "lay_out", "read"]
