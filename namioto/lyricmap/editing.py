# SPDX-License-Identifier: AGPL-3.0-only
"""Discrete lyric edits produce anchors, never raw timing changes.

Candidates replace only the operations they explicitly edit. Other confirmed operations
remain anchors; the solver decides whether the resulting partition is possible. Qt-free.
"""

from collections.abc import Sequence
from dataclasses import replace

from namioto.karaoke.operations import Drop, Match, Merge, Operation, SoundRef


def operation_at(operations: Sequence[Operation], ref: SoundRef) -> Operation | None:
    return next((operation for operation in operations if ref in operation.sounds), None)


def anchors_for(operations: Sequence[Operation], replacements: Sequence[Operation]) -> tuple[Operation, ...]:
    touched = {ref for operation in replacements for ref in operation.sounds}
    return tuple(
        operation for operation in operations if operation.confirmed and not touched.intersection(operation.sounds)
    ) + tuple(replace(operation, confirmed=True) for operation in replacements)


def drop_sound(operations: Sequence[Operation], ref: SoundRef) -> tuple[Operation, ...] | None:
    operation = operation_at(operations, ref)
    if operation is None or isinstance(operation, Drop):
        return None
    replacements: list[Operation] = [Drop(ref)]
    if isinstance(operation, Merge):
        if ref not in (operation.sounds[0], operation.sounds[-1]):
            return None
        remaining = tuple(sound for sound in operation.sounds if sound != ref)
        replacements.append(
            Merge(remaining, operation.note) if len(remaining) > 1 else Match(remaining[0], (operation.note,))
        )
    return anchors_for(operations, replacements)


def insert_sound(
    operations: Sequence[Operation], ref: SoundRef, target: Operation, fraction: float
) -> tuple[Operation, ...] | None:
    if not isinstance(operation_at(operations, ref), Drop) or isinstance(target, Drop):
        return None
    refs = tuple(sorted((*target.sounds, ref), key=lambda sound: (sound.line, sound.index)))
    if any(sound.line != ref.line for sound in refs) or [sound.index for sound in refs] != list(
        range(refs[0].index, refs[-1].index + 1)
    ):
        return None
    if isinstance(target, Merge):
        return anchors_for(operations, [Merge(refs, target.note)])
    if len(target.notes) != 1:
        return None
    return _single_note(operations, refs[0], refs[1], target.notes[0], fraction)


def _single_note(operations, left, right, note, fraction):
    if fraction < 0.25:
        replacements = [Drop(left), Match(right, (note,))]
    elif fraction > 0.75:
        replacements = [Match(left, (note,)), Drop(right)]
    else:
        replacements = [Merge((left, right), note)]
    return anchors_for(operations, replacements)


def move_boundary(
    operations: Sequence[Operation], left: SoundRef, right: SoundRef, notes: Sequence, seconds: float
) -> tuple[Operation, ...] | None:
    if left.line != right.line or right.index != left.index + 1:
        return None
    first, second = operation_at(operations, left), operation_at(operations, right)
    if first is None or second is None:
        return None
    if isinstance(first, Merge) or isinstance(second, Merge):
        if first != second or first.sounds != (left, right):
            return None
        ids = (first.note,)
    else:
        ids = tuple(
            identifier
            for operation in (first, second)
            if isinstance(operation, Match)
            for identifier in operation.notes
        )
    stream = [note for note in notes if note.id in ids]
    if not stream or len(stream) != len(ids):
        return None
    if len(stream) == 1:
        note = stream[0]
        return _single_note(operations, left, right, note.id, (seconds - note.start) / (note.end - note.start))
    split = sum(seconds >= (note.start + note.end) / 2 for note in stream)
    replacements = [
        Match(left, tuple(note.id for note in stream[:split])) if split else Drop(left),
        Match(right, tuple(note.id for note in stream[split:])) if split < len(stream) else Drop(right),
    ]
    return anchors_for(operations, replacements)
