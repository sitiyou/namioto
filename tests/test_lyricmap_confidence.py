# SPDX-License-Identifier: AGPL-3.0-only
"""Checks for operation-level confidence: each signal, and the named threshold it fails at."""

from __future__ import annotations

import pytest

from namioto.document import Note
from namioto.karaoke.operations import Drop, Match, Merge, SoundRef
from namioto.karaoke.sounds import natural_sounds
from namioto.lyricmap.confidence import FIT_ERROR_SECONDS, MARGIN_PER_SECOND, QUALITY, REST_RATIO, read
from namioto.lyricmap.raw import Raw
from namioto.lyricmap.solver import solve


def _notes(*spans):
    return [Note(60, start, end - start, id=index + 1) for index, (start, end) in enumerate(spans)]


def _readings(text, raw, notes, flagged=()):
    lines = natural_sounds(text)
    operations = solve(lines, raw, notes)
    return lines, operations, read(lines, raw, notes, operations, flagged)


def test_a_well_fitted_operation_is_not_low():
    _lines, _operations, readings = _readings("あ", [[Raw(0.0, 1.0, 0.9)]], _notes((0.0, 1.0)))
    assert len(readings) == 1
    assert not readings[0].low
    assert readings[0].quality == 0.9


def test_an_unknown_aligner_score_is_neither_high_nor_low():
    _lines, _operations, readings = _readings("あ", [[Raw(0.0, 1.0, None)]], _notes((0.0, 1.0)))
    assert readings[0].quality == QUALITY
    assert not readings[0].low


def test_a_flagged_line_lowers_confidence():
    _lines, _operations, readings = _readings("あ", [[Raw(0.0, 1.0, 0.9)]], _notes((0.0, 1.0)), [True])
    assert readings[0].quality == 0.0
    assert readings[0].low


def test_a_boundary_far_from_the_raw_evidence_is_low():
    _lines, _operations, readings = _readings("あ", [[Raw(0.5, 1.0, 0.9)]], _notes((0.0, 1.0)))
    assert readings[0].fit_error > FIT_ERROR_SECONDS
    assert readings[0].low


def test_a_note_held_past_the_raw_line_end_is_not_low():
    # the note outlasts the sung line: a held note, not a mismatch
    _lines, _operations, readings = _readings("あ", [[Raw(0.0, 0.5, 0.9)]], _notes((0.0, 1.0)))
    assert readings[0].fit_error == 0.0
    assert not readings[0].low


def test_a_note_that_ends_before_the_raw_line_end_is_low():
    # the note stops while the lyric is still sung: a real mismatch
    _lines, _operations, readings = _readings("あ", [[Raw(0.0, 1.0, 0.9)]], _notes((0.0, 0.4)))
    assert readings[0].fit_error > FIT_ERROR_SECONDS
    assert readings[0].low


@pytest.mark.parametrize(
    ("onset", "length", "fit_error"),
    [(0.9, 0.05, 0.1), (0.9, 0.2, 0.2), (1.9, 1.0, 2.8), (2.9, 0.05, 0.1), (2.9, 0.2, 0.2)],
)
def test_a_line_end_drop_uses_its_onset_boundary_for_under_run(onset, length, fit_error):
    lines = natural_sounds("あ\nい\nう")
    raw = [[Raw(0.0, 1.0, 0.9)], [Raw(onset, length, 0.9)], [Raw(3.0, 1.0, 0.9)]]
    operations = [Match(SoundRef(0, 0), (1,)), Drop(SoundRef(1, 0)), Match(SoundRef(2, 0), (2,))]
    readings = read(lines, raw, _notes((0.0, 1.0), (3.0, 4.0)), operations)
    assert readings[1].fit_error == pytest.approx(fit_error)


def test_line_end_drops_at_the_stream_edges_use_the_only_note_boundary():
    lines = natural_sounds("あ\nい\nう")
    raw = [[Raw(0.8, 0.1, 0.9)], [Raw(1.0, 1.0, 0.9)], [Raw(2.1, 0.2, 0.9)]]
    operations = [Drop(SoundRef(0, 0)), Match(SoundRef(1, 0), (1,)), Drop(SoundRef(2, 0))]
    readings = read(lines, raw, _notes((1.0, 2.0)), operations)
    assert readings[0].fit_error == pytest.approx(0.2)
    assert readings[2].fit_error == pytest.approx(0.4)


@pytest.mark.parametrize("rest", [0.0, 0.1, 8.0435, 40.0])
def test_a_line_end_drop_before_a_rest_is_not_low_confidence(rest):
    next_start = 65.3254 + rest
    _lines, operations, readings = _readings(
        "ぱい\nあ",
        [[Raw(64.62, 0.14), Raw(65.22, 0.135)], [Raw(next_start, 0.2174)]],
        _notes((64.6732, 65.3254), (next_start, next_start + 0.2174)),
    )
    assert operations[1] == Drop(SoundRef(0, 1))
    assert readings[1].fit_error == pytest.approx(0.135)
    assert not readings[0].low
    assert not readings[1].low


def test_a_tie_between_two_operations_is_a_low_margin():
    _lines, _operations, readings = _readings("あか", [[Raw(0.0, 0.75, 0.9), Raw(0.75, 0.25, 0.9)]], _notes((0.0, 1.0)))
    assert readings[0].margin < MARGIN_PER_SECOND
    assert readings[0].low


def test_a_merge_weights_fit_and_removes_the_weighted_line_end_overshoot():
    _lines, operations, readings = _readings("ない", [[Raw(0.1, 0.5, 0.9), Raw(0.6, 0.2, 0.9)]], _notes((0.0, 1.0)))
    assert operations == [Merge((SoundRef(0, 0), SoundRef(0, 1)), 1)]
    assert readings[0].fit_error == pytest.approx(0.16)
    assert not readings[0].low


def test_a_merge_weights_line_end_under_run_as_well_as_onsets():
    _lines, operations, readings = _readings("ない", [[Raw(0.1, 0.5, 0.9), Raw(0.6, 0.6, 0.9)]], _notes((0.0, 1.0)))
    assert operations == [Merge((SoundRef(0, 0), SoundRef(0, 1)), 1)]
    assert readings[0].fit_error == pytest.approx(0.32)
    assert readings[0].low


def test_empirical_weighting_does_not_hide_a_large_alignment_error():
    _lines, operations, readings = _readings("あい", [[Raw(0.0, 1.0), Raw(5.0, 1.0)]], _notes((0.0, 1.0)))
    assert operations == [Merge((SoundRef(0, 0), SoundRef(0, 1)), 1)]
    assert readings[0].fit_error == pytest.approx(7.6)
    assert readings[0].low


def test_the_margin_compares_weighted_alternatives():
    _lines, operations, readings = _readings("ない", [[Raw(0.0, 0.76), Raw(0.76, 0.24)]], _notes((0.0, 1.0)))
    assert operations == [Merge((SoundRef(0, 0), SoundRef(0, 1)), 1)]
    assert readings[0].fit_error == pytest.approx(0.208)
    assert readings[0].margin == pytest.approx(0.032)


def test_a_long_vowel_pair_has_more_margin_than_a_three_sound_merge():
    _lines, operations, readings = _readings(
        "もーす",
        [[Raw(155.5, 0.12), Raw(155.733, 0.02), Raw(155.9, 0.06)]],
        _notes((155.5428, 155.9776)),
    )
    assert operations == [Merge((SoundRef(0, 0), SoundRef(0, 1)), 1), Drop(SoundRef(0, 2))]
    assert readings[0].fit_error == pytest.approx(0.056)
    assert readings[0].margin == pytest.approx(0.2490800368)
    assert not any(reading.low for reading in readings)


def test_an_abnormal_rest_inside_a_match_is_low():
    # a match over two notes with a long rest between them and short notes around it
    _lines, _operations, readings = _readings("あ", [[Raw(0.0, 10.0, 0.9)]], _notes((0.0, 0.2), (8.0, 8.2)))
    assert readings[0].rest > REST_RATIO
    assert readings[0].low


def test_the_threshold_boundaries_are_sharp():
    assert not _readings("あ", [[Raw(0.0, 1.0, QUALITY)]], _notes((0.0, 1.0)))[2][0].low
    assert _readings("あ", [[Raw(0.0, 1.0, QUALITY - 0.01)]], _notes((0.0, 1.0)))[2][0].low
