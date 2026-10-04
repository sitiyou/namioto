# SPDX-License-Identifier: AGPL-3.0-only
"""Checks for the global mapping DP: what it chooses, what it never crosses, and what it refuses."""

from __future__ import annotations

import pytest

from namioto.document import Note
from namioto.karaoke.canonical import rebuild
from namioto.karaoke.operations import Drop, Match, Merge, SoundRef, partition
from namioto.karaoke.sounds import natural_sounds
from namioto.lyricmap.problems import MappingError
from namioto.lyricmap.raw import Raw
from namioto.lyricmap.solver import solve


def _notes(*spans):
    return [Note(60, start, end - start, id=index + 1) for index, (start, end) in enumerate(spans)]


def _solve(text, raw, notes, anchors=()):
    lines = natural_sounds(text)
    operations = solve(lines, raw, notes, anchors)
    ordered = partition(operations, [len(line.sounds) for line in lines])
    return lines, operations, ordered


def test_one_note_a_sound():
    # a sound whose raw time is on a note starts its own match
    lines, _operations, rows = _solve("あい", [[Raw(0.0, 1.0), Raw(1.0, 1.0)]], _notes((0.0, 1.0), (1.0, 2.0)))
    assert [type(operation).__name__ for operation in rows[0]] == ["Match", "Match"]


def test_a_match_holds_more_than_one_note():
    lines, _operations, rows = _solve("あ", [[Raw(0.0, 2.0)]], _notes((0.0, 1.0), (1.0, 2.0)))
    assert rows[0] == [Match(SoundRef(0, 0), (1, 2))]


def test_a_merge_takes_the_note_the_sounds_evenly_share():
    lines, _operations, rows = _solve("あい", [[Raw(0.0, 0.5), Raw(0.5, 0.5)]], _notes((0.0, 1.0)))
    assert rows[0] == [Merge((SoundRef(0, 0), SoundRef(0, 1)), 1)]


@pytest.mark.parametrize("onset", [0.8, 0.9])
def test_match_and_drop_beats_a_merge_when_the_times_disagree(onset):
    lines, operations, rows = _solve("あい", [[Raw(0.0, onset), Raw(onset, 1.0 - onset)]], _notes((0.0, 1.0)))
    assert [type(operation).__name__ for operation in rows[0]] == ["Match", "Drop"]
    assert rebuild("あい", operations) == "あい.0"


def test_a_drop_takes_the_cursor_boundary_nearest_its_onset():
    # B sits just past A's note across a rest: its own boundary is that note's end, not C's note start
    lines, _operations, rows = _solve(
        "あいか", [[Raw(0.0, 1.0), Raw(1.05, 0.15), Raw(3.0, 1.0)]], _notes((0.0, 1.0), (3.0, 4.0))
    )
    assert [type(operation).__name__ for operation in rows[0]] == ["Match", "Drop", "Match"]


def test_a_drop_consumes_no_note():
    lines, _operations, rows = _solve("あい", [[Raw(0.0, 1.0), Raw(5.0, 1.0)]], _notes((0.0, 1.0)))
    assert any(isinstance(operation, Drop) for operation in rows[0])


def test_every_sound_and_note_is_consumed_once():
    text = "胡椒[こ,しょう]は"
    lines, operations, rows = _solve(
        text,
        [[Raw(0.0, 0.3), Raw(0.3, 0.3), Raw(0.6, 0.3), Raw(0.9, 0.3)]],
        _notes((0.0, 0.3), (0.3, 0.6), (0.6, 0.9), (0.9, 1.2)),
    )
    assert rebuild(text, operations) == "胡椒[こ,しょう]は"
    assert sorted(ref.index for operation in rows[0] for ref in operation.sounds) == [0, 1, 2, 3]


def test_a_merge_never_crosses_a_container_or_a_line():
    cases = [
        ("胡椒[こ,(しょう)]", [[Raw(0.0, 1 / 3), Raw(1 / 3, 1 / 3), Raw(2 / 3, 1 / 3)]], _notes((0.0, 1.0))),
        ("世界[せ,かい]", [[Raw(0.0, 0.5), Raw(0.5, 0.25), Raw(0.75, 0.25)]], _notes((0.0, 1.0))),
        ("あ\nい", [[Raw(0.0, 0.5)], [Raw(0.5, 0.5)]], _notes((0.0, 1.0))),
    ]
    for text, raw, notes in cases:
        lines, _operations, rows = _solve(text, raw, notes)
        for operation in rows:
            if not isinstance(operation, Merge):
                continue
            keys = {
                lines[ref.line].containers[lines[ref.line].sounds[ref.index].container].key for ref in operation.sounds
            }
            assert len(keys) == 1


def test_no_character_is_banned_from_a_merge():
    # a sokuon, a long vowel and a moraic n may all be merged when the times have them share a note
    lines, _operations, rows = _solve(
        "がっこう",
        [[Raw(0.0, 0.25), Raw(0.25, 0.25), Raw(0.5, 0.25), Raw(0.75, 0.25)]],
        _notes((0.0, 1.0)),
    )
    assert rows[0] == [Merge(tuple(SoundRef(0, index) for index in range(4)), 1)]


def test_an_exact_tie_prefers_the_earlier_match():
    # merge(A, B) and match(A)+drop(B) cost the same 0.25 and have the same complexity, so the
    # solution keeping A's match (rank 0) beats the one merging (rank 1) on the earlier sound
    lines, _operations, rows = _solve("あい", [[Raw(0.0, 0.75), Raw(0.75, 0.25)]], _notes((0.0, 1.0)))
    assert [type(operation).__name__ for operation in rows[0]] == ["Match", "Drop"]


def test_a_confirmed_drop_is_forced_and_kept():
    lines, _operations, rows = _solve(
        "あい", [[Raw(0.0, 1.0), Raw(1.0, 1.0)]], _notes((0.0, 1.0)), [Drop(SoundRef(0, 0), confirmed=True)]
    )
    assert rows[0] == [Drop(SoundRef(0, 0), confirmed=True), Match(SoundRef(0, 1), (1,))]


def test_a_confirmed_match_is_a_hard_anchor():
    lines, _operations, _rows = _solve(
        "あい",
        [[Raw(0.0, 1.0), Raw(1.0, 1.0)]],
        _notes((0.0, 1.0), (1.0, 2.0)),
        [Match(SoundRef(0, 1), (2,), confirmed=True)],
    )
    operations = _rows[0]
    assert operations[-1] == Match(SoundRef(0, 1), (2,), confirmed=True)
    assert operations[0] == Match(SoundRef(0, 0), (1,))


def test_an_anchor_with_an_unknown_note_is_refused():
    with pytest.raises(MappingError):
        _solve("あ", [[Raw(0.0, 1.0)]], _notes((0.0, 1.0)), [Match(SoundRef(0, 0), (99,), confirmed=True)])


def test_an_anchor_that_crosses_another_is_refused():
    with pytest.raises(MappingError):
        _solve(
            "あい",
            [[Raw(0.0, 1.0), Raw(1.0, 1.0)]],
            _notes((0.0, 1.0), (1.0, 2.0)),
            [
                Match(SoundRef(0, 0), (1,), confirmed=True),
                Match(SoundRef(0, 0), (2,), confirmed=True),
            ],
        )


def test_missing_evidence_refuses_the_whole_mapping():
    with pytest.raises(MappingError):
        _solve("あい", [[Raw(0.0, 1.0), Raw(None, None)]], _notes((0.0, 1.0)))


def test_no_target_notes_is_refused():
    with pytest.raises(MappingError):
        _solve("あ", [[Raw(0.0, 1.0)]], [])


def test_no_lyric_sounds_is_refused():
    with pytest.raises(MappingError):
        _solve("", [], _notes((0.0, 1.0)))


def test_reversed_raw_times_are_refused():
    with pytest.raises(MappingError):
        _solve("あい", [[Raw(1.0, 1.0), Raw(0.0, 1.0)]], _notes((0.0, 1.0), (1.0, 2.0)))
