# SPDX-License-Identifier: AGPL-3.0-only
"""Checks for the raw evidence: the onset chain, the quantizer and the completeness check."""

from __future__ import annotations

import pytest

from namioto.karaoke.sounds import natural_sounds
from namioto.lyricmap.problems import MappingError
from namioto.lyricmap.raw import Raw, chain, snap_to_beats, validate


def test_the_chain_takes_each_end_from_the_next_start():
    assert chain([[Raw(0.0, 0.9), Raw(1.0, 1.4)]]) == [[Raw(0.0, 1.0), Raw(1.0, 1.4)]]


def test_snap_rounds_each_sound_on_its_own_cell():
    assert snap_to_beats([[(0.0, 0.4), (0.6, 1.0)]], 60.0) == [[(0.0, 0.0), (1.0, 1.0)]]


def test_snap_leaves_an_unaligned_line_alone():
    assert snap_to_beats([[(None, None)]], 60.0) == [[(None, None)]]


def test_validate_refuses_a_sound_with_no_time():
    with pytest.raises(MappingError):
        validate(natural_sounds("あ"), [[Raw(None, None)]])


def test_validate_refuses_reversed_starts():
    with pytest.raises(MappingError):
        validate(natural_sounds("あい"), [[Raw(1.0, 2.0), Raw(0.0, 1.0)]])
