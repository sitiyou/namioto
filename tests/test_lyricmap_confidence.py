# SPDX-License-Identifier: AGPL-3.0-only
"""Checks for operation-level confidence: each signal, and the named threshold it fails at."""

from __future__ import annotations

from namioto.document import Note
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
    _lines, _operations, readings = _readings("あ", [[Raw(0.5, 1.5, 0.9)]], _notes((0.0, 1.0)))
    assert readings[0].fit_error > FIT_ERROR_SECONDS
    assert readings[0].low


def test_a_tie_between_two_operations_is_a_low_margin():
    _lines, _operations, readings = _readings("あい", [[Raw(0.0, 0.75, 0.9), Raw(0.75, 1.0, 0.9)]], _notes((0.0, 1.0)))
    assert readings[0].margin < MARGIN_PER_SECOND
    assert readings[0].low


def test_an_abnormal_rest_inside_a_match_is_low():
    # a match over two notes with a long rest between them and short notes around it
    _lines, _operations, readings = _readings("あ", [[Raw(0.0, 10.0, 0.9)]], _notes((0.0, 0.2), (8.0, 8.2)))
    assert readings[0].rest > REST_RATIO
    assert readings[0].low


def test_the_threshold_boundaries_are_sharp():
    assert not _readings("あ", [[Raw(0.0, 1.0, QUALITY)]], _notes((0.0, 1.0)))[2][0].low
    assert _readings("あ", [[Raw(0.0, 1.0, QUALITY - 0.01)]], _notes((0.0, 1.0)))[2][0].low
