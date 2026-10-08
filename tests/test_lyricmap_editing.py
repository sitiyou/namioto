"""Discrete mapping edits preserve the operation partition model."""

from namioto.karaoke.operations import Drop, Match, Merge, SoundRef
from namioto.lyricmap.editing import drop_sound, insert_sound, move_boundary
from namioto.lyricmap.notes import TimedNote

A, B, C = (SoundRef(0, index) for index in range(3))
NOTES = (TimedNote(0, 1, 60, 1), TimedNote(1, 2, 62, 2))


def test_single_note_boundary_has_three_states():
    base = (Match(A, (1,)), Drop(B))
    assert move_boundary(base, A, B, NOTES, 0.8) == (Match(A, (1,), True), Drop(B, True))
    assert move_boundary(base, A, B, NOTES, 0.5) == (Merge((A, B), 1, True),)
    assert move_boundary(base, A, B, NOTES, 0.2) == (Drop(A, True), Match(B, (1,), True))


def test_two_note_boundary_transfers_whole_notes():
    base = (Match(A, (1,)), Match(B, (2,)))
    assert move_boundary(base, A, B, NOTES, 1.6) == (Match(A, (1, 2), True), Drop(B, True))
    assert move_boundary(base, A, B, NOTES, 0.4) == (Drop(A, True), Match(B, (1, 2), True))


def test_insert_position_can_merge_or_displace():
    target = Match(A, (1,))
    base = (target, Drop(B))
    assert insert_sound(base, B, target, 0.5) == (Merge((A, B), 1, True),)
    assert insert_sound(base, B, target, 0.2) == (Drop(A, True), Match(B, (1,), True))


def test_insertion_keeps_lyric_order_when_drop_precedes_target():
    target = Match(B, (1,))
    base = (Drop(A), target)
    assert insert_sound(base, A, target, 0.8) == (Match(A, (1,), True), Drop(B, True))


def test_insert_into_group_never_displaces_members():
    target = Merge((A, B), 1)
    assert insert_sound((target, Drop(C)), C, target, 0.01) == (Merge((A, B, C), 1, True),)


def test_no_interleaved_match_and_merge():
    target = Match(A, (1, 2))
    assert insert_sound((target, Drop(B)), B, target, 0.5) is None


def test_nonconsecutive_and_cross_line_insertions_are_rejected():
    target = Match(A, (1,))
    assert insert_sound((target, Drop(B), Drop(C)), C, target, 0.5) is None
    other = SoundRef(1, 0)
    assert insert_sound((target, Drop(other)), other, target, 0.5) is None


def test_middle_group_member_cannot_be_dropped():
    group = Merge((A, B, C), 1)
    assert drop_sound((group,), B) is None
    assert drop_sound((group,), A) == (Drop(A, True), Merge((B, C), 1, True))
    assert drop_sound((Merge((A, B), 1),), B) == (Drop(B, True), Match(A, (1,), True))


def test_drop_releases_notes_but_keeps_other_confirmed_operations():
    other = Match(C, (2,), True)
    assert drop_sound((Match(A, (1,), True), Drop(B), other), A) == (other, Drop(A, True))


def test_group_boundary_cannot_split_a_three_member_group():
    group = Merge((A, B, C), 1)
    assert move_boundary((group,), A, B, NOTES, 0.1) is None
