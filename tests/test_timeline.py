# SPDX-License-Identifier: AGPL-3.0-only
"""Checks for the `.krc` timeline: the token each mora gets, and what it refuses to align."""

from __future__ import annotations

from typing import NamedTuple

import pytest

from namioto.karaoke import (
    KrcError,
    align_tokens,
    conflicts,
    mora_lines,
    note_counts,
    snap_to_beats,
    split,
    with_counts,
)
from namioto.karaoke.model import calc_mora
from namioto.utils import kana_tokens


def _tokens(text):
    return align_tokens(mora_lines(text))


def _shape(text):
    return [(mora.base, mora.ruby, mora.token) for line in mora_lines(text) for mora in line.morae]


def test_plain_kana_is_one_token_per_mora():
    assert _shape("しんぶん") == [
        ("し", "し", "shi"),
        ("ん", "ん", "n"),
        ("ぶ", "ぶ", "bu"),
        ("ん", "ん", "n"),
    ]


def test_a_long_vowel_gets_a_token_of_its_own():
    assert _tokens("コーヒー") == ["ko", "o", "hi", "i"]
    assert _tokens("ローラ") == ["ro", "o", "ra"]


def test_a_sokuon_gets_a_token_of_its_own():
    assert _tokens("がっこう") == ["ga", "k", "ko", "u"]
    assert _tokens("ちょっと") == ["cho", "t", "to"]


def test_a_small_kana_stays_with_the_mora_before_it():
    assert _tokens("きょう") == ["kyo", "u"]
    assert _tokens("ヴァイオリン") == ["va", "i", "o", "ri", "n"]


def test_a_ruby_is_cut_into_one_token_per_mora():
    assert _shape("季節[き,せつ]は") == [
        ("季", "き", "ki"),
        ("節", "せ", "se"),
        ("", "つ", "tsu"),
        ("は", "は", "ha"),
    ]
    assert _tokens("明日[あした]を") == ["a", "shi", "ta", "o"]


def test_a_latin_run_is_one_mora_and_a_digit_is_its_own():
    assert _tokens("hello") == ["hello"]
    assert _tokens("123") == ["1", "2", "3"]
    assert _tokens("abc123") == ["abc", "1", "2", "3"]


def test_punctuation_and_symbols_add_no_mora():
    assert _tokens("わ、を") == ["wa", "o"]
    assert _tokens("あ！い") == ["a", "i"]
    assert _tokens("Ring!") == ["ring"]
    assert calc_mora("♪～!?-—") == 0
    assert calc_mora("Ring!") == 4


def test_the_track_and_every_chapter_become_rows():
    lines = mora_lines("あ\n---\n{2}い")
    assert [line.text for line in lines] == ["あ", "い"]
    assert align_tokens(lines) == ["a", "i"]


CASES = [
    "しんぶん",
    "コーヒー",
    "ラーメン",
    "がっこう",
    "きょう",
    "ちょっと",
    "ヴァイオリン",
    "季節[き,せつ]は移[うつ]ろい",
    "明日[あした]を描[えが]く",
    "映[うつ]し出[だ]す",
    "hello world",
    "abc123",
    "123",
    "One two",
    "わ、を",
    "あ！い",
    "ローラ",
    "ブランケット",
    "今日[きょう]は",
]


@pytest.mark.parametrize("text", CASES)
def test_the_tokens_are_the_folded_kana(text):
    lines = mora_lines(text)
    kana = "".join(mora.ruby for line in lines for mora in line.morae)
    assert "".join(align_tokens(lines)) == "".join(kana_tokens(kana))


def test_a_kanji_without_a_ruby_is_refused():
    with pytest.raises(KrcError):
        mora_lines("世界")


def test_a_long_vowel_or_sokuon_with_nothing_to_lean_on_is_refused():
    with pytest.raises(KrcError):
        mora_lines("ーあ")
    with pytest.raises(KrcError):
        mora_lines("あっ")


class Token(NamedTuple):
    start: float | None
    end: float | None


def test_split_cuts_the_flat_tokens_into_lines():
    lines = mora_lines("あい\nうえ")
    tokens = [Token(0.0, 0.1), Token(0.1, 0.2), Token(0.5, 0.6), Token(0.6, 0.7)]
    assert split(tokens, lines) == [[(0.0, 0.1), (0.1, 0.2)], [(0.5, 0.6), (0.6, 0.7)]]


def test_snap_to_beats_rounds_every_boundary_onto_the_grid():
    # 120 BPM is half a second a beat
    assert snap_to_beats([[(0.1, 0.6), (0.6, 1.1)]], 120.0) == [[(0.0, 0.5), (0.5, 1.0)]]


def test_snap_to_beats_keeps_two_morae_from_collapsing():
    assert snap_to_beats([[(0.1, 0.9), (0.2, 1.1)]], 120.0) == [[(0.0, 0.5), (0.5, 1.0)]]


def test_snap_to_beats_leaves_an_unaligned_line_alone():
    row = [(None, None), (1.0, 2.0)]
    assert snap_to_beats([row], 120.0) == [[(None, None), (1.0, 2.0)]]


def test_a_mora_spanning_several_notes_counts_them():
    lines = mora_lines("あん")
    times = [[(0.0, 1.0), (1.0, 8.0)]]
    assert note_counts(lines, times, [(0.0, 1.0), (1.0, 4.0), (4.0, 8.0)]) == [[1, 2]]


def test_conflicts_report_an_unaligned_mora_and_a_stray_note():
    assert conflicts(mora_lines("あ"), [[(None, None)]], [(0.0, 1.0)]) == ["あ: not aligned"]
    assert conflicts(mora_lines("あ"), [[(0.0, 1.0)]], []) == ["あ: no note between 0.000 and 1.000"]


def test_conflicts_report_a_mora_that_does_not_sit_on_its_notes():
    problems = conflicts(mora_lines("あ"), [[(0.0, 1.0)]], [(0.2, 1.0)])
    assert problems == ["あ: does not start and end on its notes"]


def test_conflicts_report_a_shared_note():
    problems = conflicts(mora_lines("あい"), [[(0.0, 1.0), (0.0, 1.0)]], [(0.0, 1.0)])
    assert any("shares a note" in problem for problem in problems)


def test_notes_line_up_within_the_tolerance():
    lines = mora_lines("あ")
    times = [[(0.0, 1.0)]]
    notes = [(0.03, 1.0)]
    assert conflicts(lines, times, notes) == []
    assert note_counts(lines, times, notes) == [[1]]


def test_with_counts_writes_a_held_mora_as_dot_n_and_drops_a_stale_one():
    assert with_counts("あん", [[1, 2]]) == "あん.2"
    assert with_counts("あん.3", [[1, 1]]) == "あん"


def test_with_counts_writes_inside_a_ruby_per_mora():
    assert with_counts("季節[き,せつ]", [[1, 2, 1]]) == "季節[き,せ.2つ]"


@pytest.mark.parametrize("text", ["あん", "季節[き,せつ]", "がっこう", "(幾千)[いくせん]"])
def test_with_counts_leaves_the_readings_alone(text):
    counts = [[1] * len(line.morae) for line in mora_lines(text)]
    again = mora_lines(with_counts(text, counts))
    assert [mora.ruby for line in again for mora in line.morae] == [
        mora.ruby for line in mora_lines(text) for mora in line.morae
    ]
