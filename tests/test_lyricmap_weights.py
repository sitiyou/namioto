# SPDX-License-Identifier: AGPL-3.0-only
"""Pattern precedence, dictionary boundaries and conservative empirical preferences."""

import pytest

from namioto.karaoke.sounds import natural_sounds
from namioto.lyricmap import weights


def _weights(text):
    return weights.merge_weights(natural_sounds(text)[0])


@pytest.mark.parametrize(
    "text", ["ない", "いい", "こう", "せい", "はい", "泣[な]い", "今日[きょう]", "もー", "字[も]ー"]
)
def test_empirical_pairs_receive_the_configured_weight(text):
    assert _weights(text)[0] == (0.8,)


def test_a_particle_and_the_next_verb_do_not_form_an_empirical_pair():
    rows = _weights("私[わたし]はいない")
    assert rows[3][0] == 1.0
    assert rows[5][0] == 0.8


@pytest.mark.parametrize("text", ["な い", "な、い", "字[ない]", "qzx[ない]"])
def test_word_boundaries_unknown_words_and_mismatched_ruby_receive_no_discount(text):
    assert _weights(text)[0] == (1.0,)


def test_an_indivisible_ruby_crossing_words_does_not_invent_a_boundary():
    rows = _weights("(私は)[わたしは]いない")
    assert rows[3][0] == 1.0
    assert rows[5][0] == 0.8


def test_a_multi_part_ruby_can_keep_connections_inside_one_word():
    rows = _weights("胡椒[こ,しょう]")
    assert rows[0] == (1.0, 1.5)
    assert rows[1] == (0.8,)


@pytest.mark.parametrize("text", ["っき", "きっ", "ッキ", "キッ", "っきちく", "きちくっ"])
def test_an_edge_sokuon_returns_its_weight_before_the_size_rule(text):
    assert _weights(text)[0][-1] == 1.5


def test_an_internal_sokuon_does_not_trigger_the_edge_rule():
    assert _weights("きっちく")[0][-1] == 2.0


@pytest.mark.parametrize(("text", "expected"), [("もーす", 1.5), ("もーー", 1.5), ("もーーー", 2.0)])
def test_large_merges_receive_no_partial_or_whole_empirical_discount(text, expected):
    assert _weights(text)[0][-1] == expected


def test_a_normal_pair_and_a_single_sound_keep_the_default_weight():
    assert _weights("あか") == ((1.0,), ())
    assert _weights("あ") == ((),)


def test_input_mapping_annotations_do_not_change_pattern_weights():
    assert _weights("泣[な]い.+") == _weights("泣[な]い")
    assert _weights("(ない).1") == _weights("ない")


def test_analysis_offsets_preserve_leading_spaces():
    assert _weights("　ない　")[0] == (0.8,)


def test_pattern_parameters_are_adjustable_without_stale_weight_caches(monkeypatch):
    _weights("もー")
    monkeypatch.setattr(weights, "EMPIRICAL_MERGE_WEIGHT", 0.7)
    monkeypatch.setattr(weights, "MERGE_SIZE_SLOPE", 0.25)
    monkeypatch.setattr(weights, "EDGE_SOKUON_MERGE_WEIGHT", 1.2)
    assert _weights("もー")[0] == (0.7,)
    assert _weights("もーーー")[0][-1] == 1.5
    assert _weights("もーーっ")[0][-1] == 1.2
