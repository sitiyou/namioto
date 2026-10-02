# SPDX-License-Identifier: AGPL-3.0-only
"""Rebuild a `.krc` from the natural Sounds and a mapping, rather than patching the input's `.N`.

The input's `.N` and `(...)` carried a mapping; the rebuild throws them away and starts from the
Sounds the text naturally reads. Each Sound's `.N` lands on the smallest Unit that still writes that
Sound whole - `(しょ).2`, never `し.2ょ` - and a merge folds its Sounds into one legal `(...).1`. A
Sound that takes one note gets no `.N` at all. The rebuilt text is parsed and flattened again; if its
Sound or token sequence moved, the rebuild is refused, so nothing that would mis-time a sound is ever
written. Qt-free.
"""

from __future__ import annotations

from collections.abc import Sequence

from namioto.karaoke.model import Group, KrcError, Line, Unit, Word
from namioto.karaoke.operations import Operation, partition
from namioto.karaoke.parser import parse
from namioto.karaoke.sounds import SoundLine, _chars, _entries, natural_sounds
from namioto.karaoke.writer import dumps


def rebuild(text: str, operations: Sequence[Operation]) -> str:
    """The canonical `.krc` for `text` under `operations`, verified to read back the same Sounds.

    `operations` must already be a partition of the natural Sounds; a mapping that is not one, or one
    whose written `.N` would move a Sound, raises `KrcError` rather than writing a broken file.
    """
    if not text.strip():
        return ""
    lines = natural_sounds(text)
    plan = partition(list(operations), [len(line.sounds) for line in lines])
    lyrics = parse(text)
    line_index = 0
    for chapter in lyrics.chapters:
        for line in chapter.lines:
            _rebuild_line(line, plan[line_index])
            line_index += 1
    rebuilt = dumps(lyrics, dotted=True)
    _verify(lines, rebuilt)
    return rebuilt


def _rebuild_line(line: Line, operations: list[Operation]) -> None:
    """Replace the units of one line with the ones the mapping writes."""
    cursor = 0
    new_units: list[Unit] = []
    run: list[str] = []

    def flush() -> None:
        nonlocal cursor
        if not run:
            return
        taken = _take(operations, cursor, len(_entries(run)))
        cursor += len(taken)
        new_units.extend(_build(run, taken))
        run.clear()

    for unit in line.units:
        if unit.ruby is None:
            run.extend(_chars(unit))
            continue
        flush()
        for part in unit.ruby.parts:
            atoms = [char for inner in part for char in _chars(inner)]
            taken = _take(operations, cursor, len(_entries(atoms)))
            cursor += len(taken)
            part[:] = _build(atoms, taken)
        new_units.append(unit)
    flush()
    if cursor != len(operations):
        raise KrcError("the mapping does not cover this line's sounds")
    line.units = new_units


def _take(operations: list[Operation], cursor: int, count: int) -> list[Operation]:
    taken: list[Operation] = []
    found = 0
    at = cursor
    while found < count:
        operation = operations[at]
        taken.append(operation)
        found += len(operation.sounds)
        at += 1
    if found != count:
        raise KrcError("the mapping does not cover this container's sounds")
    return taken


def _build(atoms: list[str], operations: list[Operation]) -> list[Unit]:
    """The units one container's characters become: an override lands on the Sound's whole range."""
    entries = _entries(atoms)
    starts: dict[int, tuple[int, int | None]] = {}
    at = 0
    for operation in operations:
        low = entries[at][1]
        high = entries[at + len(operation.sounds) - 1][2]
        starts[low] = (high, operation.dot)
        at += len(operation.sounds)
    units: list[Unit] = []
    index = 0
    while index < len(atoms):
        if index not in starts:
            units.append(Unit(Word(atoms[index])))
            index += 1
            continue
        high, override = starts[index]
        units.extend(_units(atoms[index : high + 1], override))
        index = high + 1
    return units


def _units(chars: list[str], override: int | None) -> list[Unit]:
    """A Sound's characters as one unit: a word, or a group when an override must write them whole.

    With no override, the characters stay words of their own, so `しょ` is not needlessly grouped.
    """
    if override is None:
        return [Unit(Word(char)) for char in chars]
    if len(chars) == 1:
        return [_overridden(Unit(Word(chars[0])), override)]
    return [_overridden(Unit(Group([Word(char) for char in chars])), override)]


def _overridden(unit: Unit, override: int) -> Unit:
    unit.override = override
    return unit


def _verify(before: list[SoundLine], rebuilt: str) -> None:
    """The rebuilt text must read back as the same Sounds, tokens and readings as the input."""
    try:
        after = natural_sounds(rebuilt)
    except (KrcError, ValueError) as error:
        raise KrcError(f"round_trip_mismatch: the written lyrics do not read back: {error}") from None
    if len(before) != len(after) or any(_shape(one) != _shape(other) for one, other in zip(before, after, strict=True)):
        raise KrcError("round_trip_mismatch: writing the mapping back would move a sound")


def _shape(line: SoundLine) -> list[tuple]:
    return [(sound.reading, sound.base, sound.token, sound.rubied, sound.first) for sound in line.sounds]
