# SPDX-License-Identifier: AGPL-3.0-only
"""Checks for read-only mode: the `.krc`'s own `.N` and groups, and nothing estimated."""

from __future__ import annotations

from namioto.document import Note
from namioto.lyricmap.faithful import consumes, lay_out, read
from namioto.lyricmap.verify import faithful_gate


def _notes(*spans):
    return [Note(60, start, end - start, id=index + 1) for index, (start, end) in enumerate(spans)]


def test_a_dot_holds_one_sound_over_several_notes():
    assert read("あ.2", _notes((0.0, 1.0), (1.0, 2.0))) == [[(0.0, 2.0)]]


def test_lay_out_names_the_notes_each_sound_takes():
    spans, ids = lay_out("あ.2", _notes((0.0, 1.0), (1.0, 2.0)))
    assert spans == [[(0.0, 2.0)]]
    assert ids == [[(1, 2)]]


def test_lay_out_shares_one_note_between_the_sounds_that_tile_it():
    spans, ids = lay_out("(あい).1", _notes((0.0, 1.0)))
    assert spans == [[(0.0, 0.5), (0.5, 1.0)]]
    assert ids == [[(1,), (1,)]]


def test_a_group_slots_several_sounds_onto_one_note():
    assert read("(あい).1", _notes((0.0, 1.0))) == [[(0.0, 0.5), (0.5, 1.0)]]


def test_small_kana_stays_one_sound():
    assert read("京[きょう]", _notes((0.0, 1.0), (1.0, 2.0))) == [[(0.0, 1.0), (1.0, 2.0)]]


def test_a_grouped_ruby_keeps_its_own_dot():
    rows = read("青色[あ,(お).2]", _notes((0.0, 1.0), (1.0, 2.0), (2.0, 3.0)))
    assert rows == [[(0.0, 1.0), (1.0, 3.0)]]


def test_notes_running_out_leave_no_time():
    assert read("あい", _notes((0.0, 1.0))) == [[(0.0, 1.0), None]]


def test_an_input_dot_is_respected_even_where_edit_mode_ignores_it():
    assert read("あ.0", _notes((0.0, 1.0))) == [[None]]


def test_consumes_counts_the_slots_the_reading_asks_for():
    assert consumes("あ.2", _notes((0.0, 1.0), (1.0, 2.0))) == 2
    assert consumes("(あい).1", _notes((0.0, 1.0))) == 1
    assert consumes("あい", _notes((0.0, 1.0), (1.0, 2.0))) == 2
    assert consumes("", []) == 0


def test_the_read_only_subtitle_gate():
    notes = _notes((0.0, 1.0))
    assert faithful_gate(read("あ", notes), notes).open()
    assert faithful_gate(read("あい", notes), notes).counts() == {"incomplete_alignment": 1}
    assert faithful_gate(read("あ", []), []).counts() == {"no_target_notes": 1, "incomplete_alignment": 1}
    assert faithful_gate(read("あ", notes), notes, filtered=notes).counts() == {"filtered_note": 1}
