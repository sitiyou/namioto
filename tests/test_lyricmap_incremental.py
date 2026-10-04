# SPDX-License-Identifier: AGPL-3.0-only
"""Local solves reuse untouched operations, preserve anchors, and fall back on structural edits."""

from dataclasses import replace

import pytest

from namioto.karaoke.operations import Match, SoundRef, partition
from namioto.karaoke.sounds import natural_sounds
from namioto.lyricmap import solver
from namioto.lyricmap.notes import TimedNote
from namioto.lyricmap.problems import MappingError
from namioto.lyricmap.raw import Raw
from namioto.lyricmap.solver import MappingState, solve


def _song(anchors=()):
    lines = natural_sounds("\n".join(["あい"] * 9))
    raw = [[Raw(row * 4.0 + column, 1.0, 0.9) for column in range(2)] for row in range(9)]
    notes = [TimedNote(sound.onset, sound.reference_end, 60, index + 1) for index, sound in enumerate(sum(raw, []))]
    operations = solve(lines, raw, notes, anchors)
    state = MappingState.capture(lines, raw, notes, anchors, operations)
    return lines, raw, notes, state


def _segments(monkeypatch):
    calls = []
    original = solver._segment

    def record(context, sa, sb, na, nb, forced):
        calls.append((sa, sb, na, nb))
        return original(context, sa, sb, na, nb, forced)

    monkeypatch.setattr(solver, "_segment", record)
    return calls


def test_an_onset_edit_only_solves_three_lines(monkeypatch):
    lines, raw, notes, state = _song()
    raw[4][1] = raw[4][1]._replace(onset=17.1)
    expected = solve(lines, raw, notes)
    calls = _segments(monkeypatch)
    operations = solve(lines, raw, notes, previous=state)
    assert calls == [(6, 12, 6, 12)]
    assert operations == expected
    assert operations[0] is state.operations[0]
    assert operations[-1] is state.operations[-1]
    assert partition(operations, [2] * 9)
    assert sum(op.slots for op in operations) == len(notes)


@pytest.mark.parametrize("field", ["start", "end"])
def test_note_time_edits_reuse_the_outer_mapping(monkeypatch, field):
    lines, raw, notes, state = _song()
    notes[8] = notes[8]._replace(**{field: getattr(notes[8], field) + 0.1})
    expected = solve(lines, raw, notes)
    calls = _segments(monkeypatch)
    operations = solve(lines, raw, notes, previous=state)
    assert calls == [(6, 12, 6, 12)]
    assert operations == expected
    assert operations[0] is state.operations[0]
    assert operations[-1] is state.operations[-1]


def test_unchanged_inputs_do_not_run_the_dp(monkeypatch):
    lines, raw, notes, state = _song()
    calls = _segments(monkeypatch)
    assert solve(lines, raw, notes, previous=state) == list(state.operations)
    assert calls == []


@pytest.mark.parametrize("edit", ["insert", "delete", "reorder", "pitch", "lyrics"])
def test_structural_changes_use_a_full_solve(monkeypatch, edit):
    lines, raw, notes, state = _song()
    if edit == "insert":
        notes.insert(9, TimedNote(16.5, 16.7, 60, 99))
    elif edit == "delete":
        del notes[8]
    elif edit == "reorder":
        notes[8], notes[9] = notes[9], notes[8]
    elif edit == "pitch":
        notes[8] = notes[8]._replace(pitch=61)
    else:
        lines = natural_sounds("\n".join(["あか"] * 9))
    expected = solve(lines, raw, notes)
    calls = _segments(monkeypatch)
    assert solve(lines, raw, notes, previous=state) == expected
    assert calls == [(0, 18, 0, len(notes))]


def test_local_solves_keep_confirmed_matches_and_drops(monkeypatch):
    from namioto.karaoke.operations import Drop

    anchors = (Match(SoundRef(4, 0), (9, 10), confirmed=True), Drop(SoundRef(4, 1), confirmed=True))
    lines, raw, notes, state = _song(anchors)
    raw[4][0] = raw[4][0]._replace(onset=16.1)
    calls = _segments(monkeypatch)
    operations = solve(lines, raw, notes, anchors, state)
    assert all(anchor in operations for anchor in anchors)
    assert all(6 <= sa <= sb <= 12 for sa, sb, _na, _nb in calls)
    assert operations[0] is state.operations[0]
    assert sum(op.slots for op in operations) == len(notes)


def test_releasing_a_changed_sound_anchor_is_local(monkeypatch):
    anchor = Match(SoundRef(4, 0), (9,), confirmed=True)
    lines, raw, notes, state = _song((anchor,))
    raw[4][0] = raw[4][0]._replace(onset=16.1)
    calls = _segments(monkeypatch)
    operations = solve(lines, raw, notes, previous=state)
    assert calls == [(6, 12, 6, 12)]
    assert not any(op.confirmed for op in operations)
    assert operations[0] is state.operations[0]


def test_an_anchor_outside_the_old_note_boundary_expands_to_a_full_solve(monkeypatch):
    lines, raw, notes, state = _song()
    anchor = Match(SoundRef(4, 0), tuple(range(9, 19)), confirmed=True)
    calls = []
    original = solver._solve_range

    def record(context, pinned, forced, sa, sb, na, nb):
        calls.append((sa, sb, na, nb))
        return original(context, pinned, forced, sa, sb, na, nb)

    monkeypatch.setattr(solver, "_solve_range", record)
    operations = solve(lines, raw, notes, (anchor,), state)
    assert calls[0] == (6, 12, 6, 12)
    assert calls[-1] == (0, 18, 0, 18)
    assert len(calls) == 4
    assert anchor in operations
    assert sum(op.slots for op in operations) == len(notes)


def test_invalid_anchors_are_not_hidden_by_the_cache():
    lines, raw, notes, state = _song()
    with pytest.raises(MappingError, match="not in the stream"):
        solve(lines, raw, notes, (Match(SoundRef(4, 0), (99,), confirmed=True),), state)


@pytest.mark.parametrize("ids", [(9, 11), (10, 9), (9, 9)])
def test_nonconsecutive_anchor_notes_are_refused_with_or_without_a_cache(ids):
    lines, raw, notes, state = _song()
    anchor = Match(SoundRef(4, 0), ids, confirmed=True)
    for previous in (None, state):
        with pytest.raises(MappingError, match="notes do not run on"):
            solve(lines, raw, notes, (anchor,), previous)


def test_invalid_raw_times_are_not_hidden_by_the_cache():
    lines, raw, notes, state = _song()
    raw[4][0] = Raw(None, None)
    with pytest.raises(MappingError, match="no raw evidence"):
        solve(lines, raw, notes, previous=state)


def test_empty_sound_lines_do_not_break_local_boundaries():
    lines, raw, notes, state = _song()
    lines = natural_sounds("\n".join(["！？" if row == 3 else "あい" for row in range(9)]))
    raw[3] = []
    operations = solve(lines, raw, notes)
    state = MappingState.capture(lines, raw, notes, (), operations)
    raw[4][1] = raw[4][1]._replace(onset=17.1)
    local = solve(lines, raw, notes, previous=state)
    assert partition(local, [len(line.sounds) for line in lines])
    assert sum(op.slots for op in local) == len(notes)


def test_cached_note_times_are_immutable():
    from namioto.document import Note

    lines, raw, notes, state = _song()
    mutable = [Note(n.pitch, n.start, n.end - n.start, id=n.id) for n in notes]
    snapshot = MappingState.capture(lines, raw, mutable, (), state.operations)
    mutable[8].start += 0.1
    assert snapshot.notes[8].start == 16.0
    assert replace(snapshot, notes=state.notes) == state
