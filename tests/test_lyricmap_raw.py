# SPDX-License-Identifier: AGPL-3.0-only
"""Checks for onset evidence, reference durations, quantization and completeness."""

from __future__ import annotations

import pytest

from namioto.karaoke.sounds import natural_sounds
from namioto.lyricmap.problems import MappingError
from namioto.lyricmap.raw import Raw, from_spans, snap_to_beats, validate


def test_reference_durations_come_from_each_alignment_span():
    assert from_spans([[(0.0, 0.9), (1.0, 1.4), (None, None)]]) == [
        [(0.0, 0.9), (1.0, pytest.approx(0.4)), (None, None)]
    ]


def test_the_reference_end_follows_the_onset_with_the_same_duration():
    sound = Raw(10.0, 0.2, 0.9)
    moved = sound._replace(onset=10.5)
    assert moved.raw_length == sound.raw_length
    assert moved.reference_end == pytest.approx(10.7)


def test_snap_changes_only_onsets_and_allows_coincident_sounds():
    raw = [[(0.0, 0.4), (0.1, 0.2), (0.6, 0.4)]]
    assert snap_to_beats(raw, 60.0) == [[(0.0, 0.4), (0.0, 0.2), (1.0, 0.4)]]
    assert raw == [[(0.0, 0.4), (0.1, 0.2), (0.6, 0.4)]]


def test_snap_never_produces_a_negative_onset():
    assert snap_to_beats([[(0.0, 0.1)]], 60.0, offset=-0.1) == [[(0.0, 0.1)]]


def test_snap_leaves_an_unaligned_line_alone():
    assert snap_to_beats([[(None, None)]], 60.0) == [[(None, None)]]


@pytest.mark.parametrize(
    "sound",
    [Raw(None, None), Raw(0.0, None), Raw(float("nan"), 0.1), Raw(0.0, float("inf")), Raw(-0.1, 0.1), Raw(0.0, -0.1)],
)
def test_validate_refuses_invalid_evidence(sound):
    with pytest.raises(MappingError, match="raw"):
        validate(natural_sounds("あ"), [[sound]])


def test_reference_durations_do_not_constrain_neighbouring_onsets():
    validate(natural_sounds("あい"), [[Raw(1.0, 10.0), Raw(1.0, 0.0)]])


def test_validate_refuses_reversed_onsets_across_lines():
    with pytest.raises(MappingError, match="order"):
        validate(natural_sounds("あ\nい"), [[Raw(1.0, 0.1)], [Raw(0.0, 0.1)]])


def test_validate_refuses_missing_lines():
    with pytest.raises(MappingError, match="line counts"):
        validate(natural_sounds("あ\nい"), [[Raw(1.0, 0.1)]])
