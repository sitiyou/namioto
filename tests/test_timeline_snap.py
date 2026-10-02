# SPDX-License-Identifier: AGPL-3.0-only
"""The mode the whole-line snap should work in: each note is covered, a group's members split the
note into even shares, and a sound squeezed out of its note falls to `.0` instead of joining a group.

Costs are beside each case, so the choice the DP makes can be checked by hand:
  group of k on note (s, e):  sum over its sounds of |onset - (s + p * (e - s) / k)|
  a sound left out (`.0`):    its distance to the next sound's own landing, or the line's end
  a note held past the group: the holder's own |onset - (s of the held note)|
  a single sound on a note its own time does not reach: a fraction of a frame on top
"""

from namioto.karaoke import map_sounds, sound_lines


def _found(text, times, notes):
    return map_sounds(sound_lines(text), times, notes, text)[0]


def test_each_sound_takes_its_own_note_when_the_onsets_sit_on_them():
    # onsets 0.0 / 1.0 / 2.0 on (0,1) (1,2) (2,3): one owner each, cost 0
    found = _found(
        "あいう",
        [[(0.0, 1.0), (1.0, 2.0), (2.0, 3.0)]],
        [(0.0, 1.0), (1.0, 2.0), (2.0, 3.0)],
    )
    assert [p.notes for p in found] == [(0,), (1,), (2,)]
    assert [p.group for p in found] == [-1, -1, -1]
    assert not any(p.zero for p in found)


def test_two_spread_sounds_split_one_note_at_its_halves():
    # あ 0.1, い 0.6 on note (0,1): halves 0.0 / 0.5 -> 0.1 + 0.1 = 0.2
    # leaving い out costs |0.6 - 1.0| = 0.4, so the group is cheaper
    found = _found("あいう", [[(0.1, 0.5), (0.6, 0.8), (1.0, 2.0)]], [(0.0, 1.0), (1.0, 2.0)])
    assert [p.notes for p in found] == [(0,), (0,), (1,)]
    assert [p.group for p in found] == [0, 0, -1]


def test_a_sound_squeezed_against_the_next_falls_to_zero():
    # あ 0.2, い 0.9, う 1.05 on (0,1) (1,2):
    #   あ い.0 う: 0.2 + |0.9 - 1.05| + 0.05          = 0.40   <- cheapest
    #   あ い.0 う (the other way): 0.2 + 0.1 + 0.15    = 0.45
    #   あ (いう) on note 1 halves 1.0 / 1.5: 0.2 + 0.1 + 0.45 = 0.75
    found = _found("あいう", [[(0.2, 0.6), (0.9, 1.0), (1.05, 1.9)]], [(0.0, 1.0), (1.0, 2.0)])
    assert [p.notes for p in found] == [(0,), (), (1,)]
    assert [p.zero for p in found] == [False, True, False]
    assert [p.group for p in found] == [-1, -1, -1]


def test_two_spread_sounds_still_group_when_the_note_is_wide_enough():
    # あ 0.2, い 0.9, う 1.4 on (0,1) (1,2):
    #   あ (いう) on note 1 halves 1.0 / 1.5: 0.2 + 0.1 + 0.1 = 0.40   <- cheapest
    #   あ い.0 う: 0.2 + 0.5 + 0.4 = 1.10
    found = _found("あいう", [[(0.2, 0.6), (0.9, 1.2), (1.4, 1.9)]], [(0.0, 1.0), (1.0, 2.0)])
    assert [p.notes for p in found] == [(0,), (1,), (1,)]
    assert [p.group for p in found] == [-1, 1, 1]


def test_the_earlier_of_two_onsets_on_one_note_falls_to_zero():
    # あ 0.0, い 0.02, う 1.0 on (0,1) (1,2): あ and い both land on note 0's start (0.0 + 0.02 =
    # 0.02), so the earlier あ takes no length, and う takes note 1
    found = _found("あいう", [[(0.0, 0.5), (0.02, 0.1), (1.0, 2.0)]], [(0.0, 1.0), (1.0, 2.0)])
    assert [p.notes for p in found] == [(), (0,), (1,)]
    assert [p.zero for p in found] == [True, False, False]


def test_a_last_sound_takes_the_note_it_is_nearest_rather_than_the_line_end():
    # あ takes note 0 for nothing and holds note 1 for its own |0.0 - 1.0| = 1.0; い's 2.5 costs 0.5
    # to note 2's start, while あ holding note 2 too would add its |0.0 - 2.0| = 2.0
    found = _found("あい", [[(0.0, 2.0), (2.5, 3.0)]], [(0.0, 1.0), (1.0, 2.0), (2.0, 3.0)])
    assert [p.notes for p in found] == [(0, 1), (2,)]
    assert not any(p.red for p in found)
    assert not any(p.red for p in found)


def test_three_sounds_split_one_note_into_thirds():
    # 0.0 / 0.3 / 0.6 on (0,1): thirds 0.0 / 0.333 / 0.667 -> 0 + 0.033 + 0.067 = 0.1
    # leaving out any one of them costs at least 0.3
    found = _found("あいう", [[(0.0, 0.3), (0.3, 0.5), (0.6, 0.9)]], [(0.0, 1.0)])
    assert [p.notes for p in found] == [(0,), (0,), (0,)]
    assert [p.group for p in found] == [0, 0, 0]
    assert not any(p.zero for p in found)


def test_a_sound_whose_onset_falls_in_a_rest_is_read_at_the_next_note_and_falls_to_zero():
    # onsets あ 0.0, い 1.0, う 2.0 on (0,1) (2,3): い sits in the rest, so it reads at note 1's start
    # (2.0) and leaving it out is free; keeping it on note 1 would cost |1.0 - 2.0| = 1.0
    found = _found("あいう", [[(0.0, 1.0), (1.0, 2.01), (2.0, 3.0)]], [(0.0, 1.0), (2.0, 3.0)])
    assert [p.notes for p in found] == [(0,), (), (1,)]
    assert [p.zero for p in found] == [False, True, False]


def test_the_earlier_of_two_onsets_on_one_note_falls_to_zero_for_a_sokuon_too():
    # あ 0.0, っ 0.05, い 1.0: あ and っ both land on note 0's start (0.0 + 0.05 = 0.05), so the
    # earlier あ takes no length and the sokuon keeps the note
    found = _found("あっい", [[(0.0, 0.6), (0.05, 0.2), (1.0, 2.0)]], [(0.0, 1.0), (1.0, 2.0)])
    assert [p.notes for p in found] == [(), (0,), (1,)]
    assert [p.zero for p in found] == [True, False, False]
