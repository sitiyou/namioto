# SPDX-License-Identifier: AGPL-3.0-only
"""Checks for the `.krc` timeline: the token each mora gets, and what it refuses to align."""

from __future__ import annotations

from typing import NamedTuple

import pytest

from namioto.karaoke import (
    KrcError,
    align_tokens,
    assign_by_order,
    assign_by_time,
    conflicts,
    group_morae,
    map_morae,
    mora_lines,
    mora_ok,
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
        ("節", "つ", "tsu"),
        ("は", "は", "ha"),
    ]
    assert _tokens("明日[あした]を") == ["a", "shi", "ta", "o"]


def test_a_label_is_the_word_itself_without_a_ruby():
    assert [mora.label for line in mora_lines("だから") for mora in line.morae] == ["だ", "か", "ら"]
    assert [mora.label for line in mora_lines("hello") for mora in line.morae] == ["hello"]
    # the small kana stays one mora with the kana before it, and the block reads the whole surface
    assert [mora.label for line in mora_lines("きょう") for mora in line.morae] == ["きょ", "う"]
    assert [mora.label for line in mora_lines("ワイドショー") for mora in line.morae] == [
        "ワ",
        "イ",
        "ド",
        "ショ",
        "ー",
    ]


def test_a_label_puts_the_ruby_beside_the_base_unit_it_reads():
    # a comma ruby reads one kanji per part: the word opens with `()` and the rest wear `[]`
    assert [mora.label for line in mora_lines("世界[せ,かい]") for mora in line.morae] == ["せ(世)", "か[界]", "い[界]"]
    # one ruby for the whole run: the kanji is indivisible, so every mora of it wears `()`
    assert [mora.label for line in mora_lines("百合[ゆり]") for mora in line.morae] == ["ゆ(百合)", "り(百合)"]


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


def test_snap_to_beats_rounds_onto_the_offset_grid():
    assert snap_to_beats([[(0.1, 0.6), (0.6, 1.1)]], 120.0, 1.0, 0.25) == [[(0.25, 0.75), (0.75, 1.25)]]


def test_snap_to_beats_gives_a_mora_that_rounds_to_one_cell_no_length():
    # both ends of the first mora fall in the 0.0 cell, so it comes back with no length
    assert snap_to_beats([[(0.05, 0.2), (0.2, 0.3)]], 120.0) == [[(0.0, 0.0), (0.0, 0.5)]]


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


def test_mora_ok_marks_each_mora_that_sits_on_its_own_notes():
    lines = mora_lines("あん")
    notes = [(0.0, 1.0), (1.0, 2.0), (2.0, 3.0)]
    assert mora_ok(lines, [[(0.0, 1.0), (1.0, 3.0)]], notes) == [[True, True]]
    assert mora_ok(lines, [[(0.0, 1.0), (1.0, 3.0)]], [(0.0, 1.0)]) == [[True, False]]
    assert mora_ok(lines, [[(None, None), (1.0, 3.0)]], notes) == [[False, True]]
    assert mora_ok(lines, [[(0.2, 1.0), (1.0, 3.0)]], notes) == [[False, True]]
    assert mora_ok(lines, [[(0.0, 1.0), (0.0, 1.0)]], [(0.0, 1.0)]) == [[False, False]]
    assert mora_ok(lines, [[(0.0, 1.0), (1.0, 1.0)]], [(0.0, 1.0)]) == [[True, True]]  # no length, nothing to sit on


def test_a_mora_of_no_length_is_not_a_conflict():
    assert conflicts(mora_lines("あん"), [[(0.0, 1.0), (1.0, 1.0)]], [(0.0, 1.0)]) == []


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


def test_a_group_of_morae_becomes_one_word_that_covers_one_note():
    # コー is two morae of one sound, and the one note under them is one cell for the pair
    notes = [(0.0, 1.0), (1.0, 2.0)]
    times = [[(0.0, 0.5), (0.5, 1.0), (1.0, 1.5), (1.5, 2.0)]]
    assert conflicts(mora_lines("コーヒー"), times, notes)  # a mora of one word each misses the note

    text, folded = group_morae("コーヒー", times, 0, 0, 1)
    text, folded = group_morae(text, folded, 0, 1, 2)
    assert text == "(コー)(ヒー)"
    assert folded == [[(0.0, 1.0), (1.0, 2.0)]]  # the morae it was made of left the span they shared
    lines = mora_lines(text)
    assert [mora.token for mora in lines[0].morae] == ["koo", "hii"]  # the aligner reads the same sound
    assert conflicts(lines, folded, notes) == []  # one word may hold one note
    assert note_counts(lines, folded, notes) == [[1, 1]]
    assert with_counts(text, note_counts(lines, folded, notes)) == "(コー).1(ヒー).1"


def test_a_group_steps_over_a_small_kana_that_is_no_mora_of_its_own():
    # ショ is one mora of the two kana シ and ョ, so grouping it with ー takes both written words
    times = [[(0.0, 1.0), (1.0, 2.0), (2.0, 3.0), (3.0, 4.0), (4.0, 5.0)]]
    text, folded = group_morae("ワイドショー", times, 0, 3, 4)
    assert text == "ワイド(ショー)"
    assert folded == [[(0.0, 1.0), (1.0, 2.0), (2.0, 3.0), (3.0, 5.0)]]
    assert [mora.token for mora in mora_lines(text)[0].morae] == ["wa", "i", "do", "shoo"]


def test_a_group_steps_over_a_small_kana_but_still_takes_whole_words():
    with pytest.raises(KrcError):
        group_morae("あ、い", [[(0.0, 1.0), (1.0, 2.0)]], 0, 0, 1)  # the 、 is no part of a group


def test_grouping_a_mora_of_no_length_hands_it_the_previous_note_span():
    # い has no length of its own and nothing in front of it, so あ takes it in
    text, folded = group_morae("あい", [[(0.0, 1.0), (1.0, 1.0)]], 0, 0, 1)
    assert text == "(あい)"
    assert folded == [[(0.0, 1.0)]]  # the group takes the previous note's span


def test_grouping_refuses_a_ruby_a_lone_mora_and_times_that_do_not_count_them():
    with pytest.raises(KrcError):
        group_morae("世界[せ,かい]", [[(0.0, 1.0), (1.0, 2.0)]], 0, 0, 1)  # the morae a ruby reads
    with pytest.raises(KrcError):
        group_morae("あい", [[(0.0, 1.0), (1.0, 2.0)]], 0, 0, 0)  # one mora is no group
    with pytest.raises(KrcError):
        group_morae("あい", [[(0.0, 1.0)]], 0, 0, 1)  # times that do not count the morae


@pytest.mark.parametrize("text", ["あん", "季節[き,せつ]", "がっこう", "(幾千)[いくせん]"])
def test_with_counts_leaves_the_readings_alone(text):
    counts = [[1] * len(line.morae) for line in mora_lines(text)]
    again = mora_lines(with_counts(text, counts))
    assert [mora.ruby for line in again for mora in line.morae] == [
        mora.ruby for line in mora_lines(text) for mora in line.morae
    ]


def test_blocks_pair_with_the_notes_in_order_until_one_runs_out():
    assert assign_by_order(3, 5) == [0, 1, 2]
    assert assign_by_order(5, 3) == [0, 1, 2, None, None]
    assert assign_by_order(0, 3) == []
    assert assign_by_order(3, 0) == [None, None, None]


def test_an_alignment_puts_each_block_on_the_note_holding_its_onset():
    notes = [(0.0, 1.0), (1.0, 2.0), (2.0, 3.0), (3.0, 4.0)]
    assert assign_by_time(notes, notes) == [0, 1, 2, 3]


def test_a_note_no_block_reaches_is_skipped():
    notes = [(0.0, 1.0), (1.0, 2.0), (2.0, 3.0)]
    assert assign_by_time([(0.0, 0.5), (2.0, 2.5)], notes) == [0, 2]


def test_several_blocks_inside_one_note_group_on_it():
    notes = [(0.0, 4.0)]
    assert assign_by_time([(0.0, 1.0), (1.0, 2.0), (2.0, 3.0)], notes) == [0, 0, 0]


def test_a_block_in_a_rest_takes_the_nearest_note():
    assert assign_by_time([(2.0, 2.5)], [(0.0, 1.0), (3.0, 4.0)]) == [1]


def test_every_block_gets_a_note_and_the_match_is_monotone():
    notes = [(0.0, 1.0), (1.0, 2.0), (2.0, 3.0)]
    noisy = [(0.5, 0.6), (2.5, 2.6), (1.5, 1.6)]  # out of order: the times cannot reorder the blocks
    found = assign_by_time(noisy, notes)
    assert all(index is not None for index in found)
    assert found == sorted(found)


def test_no_notes_leaves_every_block_unassigned():
    assert assign_by_time([(0.0, 1.0)], []) == [None]
    assert assign_by_time([], [(0.0, 1.0)]) == []


def test_without_alignment_the_morae_and_notes_pair_one_for_one():
    found = map_morae(mora_lines("あいう"), [[(None, None)] * 3], [(0.0, 1.0), (1.0, 2.0)], aligned=False)
    assert [placement.span for placement in found[0]] == [(0.0, 1.0), (1.0, 2.0), (None, None)]
    assert [placement.notes for placement in found[0]] == [(0,), (1,), ()]


def test_a_mora_covers_the_note_its_time_overlaps():
    found = map_morae(mora_lines("あい"), [[(0.0, 1.0), (1.0, 2.0)]], [(0.0, 1.0), (1.0, 2.0)], "あい")
    assert [placement.notes for placement in found[0]] == [(0,), (1,)]
    assert [placement.span for placement in found[0]] == [(0.0, 1.0), (1.0, 2.0)]
    assert not any(placement.red for placement in found[0])


def test_a_long_mora_covers_every_note_it_overlaps():
    notes = [(0.0, 1.0), (1.0, 2.0), (2.0, 3.0)]
    found = map_morae(mora_lines("あい"), [[(0.0, 2.0), (2.0, 3.0)]], notes, "あい")
    assert found[0][0].notes == (0, 1)
    assert found[0][0].span == (0.0, 2.0)
    assert not found[0][0].red
    assert found[0][1].notes == (2,)


def test_two_morae_that_share_a_note_equally_group_on_it():
    times = [[(0.0, 1.0), (1.0, 2.0)]]
    found = map_morae(mora_lines("あい"), times, [(0.0, 2.0)], "あい")
    assert [placement.notes for placement in found[0]] == [(0,), (0,)]
    assert [placement.span for placement in found[0]] == [(0.0, 1.0), (1.0, 2.0)]


def test_the_smaller_share_of_a_note_falls_to_no_length():
    times = [[(0.0, 1.8), (1.8, 2.0)]]
    found = map_morae(mora_lines("あい"), times, [(0.0, 2.0)], "あい")
    assert found[0][0].notes == (0,)
    assert found[0][1].zero is True
    assert found[0][1].span == (1.8, 1.8)


def test_a_note_no_mora_reaches_is_given_to_the_one_before_and_doubted():
    notes = [(0.0, 1.0), (1.0, 2.0), (2.0, 3.0)]
    found = map_morae(mora_lines("あい"), [[(0.0, 1.0), (2.0, 3.0)]], notes, "あい")
    assert found[0][0].notes == (0, 1)
    assert found[0][0].red is True
    assert found[0][1].notes == (2,)


def test_a_note_before_the_first_mora_is_given_to_it_and_doubted():
    done = map_morae(mora_lines("あ"), [[(1.0, 2.0)]], [(0.0, 1.0), (1.0, 2.0)], "あ")
    assert done[0][0].notes == (0, 1)
    assert done[0][0].span == (0.0, 2.0)
    assert done[0][0].red is True


def test_a_note_after_the_last_mora_is_given_to_it_and_doubted():
    done = map_morae(mora_lines("あ"), [[(0.0, 1.0)]], [(0.0, 1.0), (1.0, 2.0)], "あ")
    assert done[0][0].notes == (0, 1)
    assert done[0][0].span == (0.0, 2.0)
    assert done[0][0].red is True


def test_a_mora_that_covers_no_note_falls_to_no_length_without_doubt():
    done = map_morae(mora_lines("あい"), [[(0.0, 1.0), (3.0, 3.5)]], [(0.0, 1.0)], "あい")
    assert done[0][0].notes == (0,)
    assert done[0][1].zero is True
    assert done[0][1].red is False


def test_a_flagged_line_is_doubted_whole():
    done = map_morae(mora_lines("あい"), [[(0.0, 1.0), (1.0, 2.0)]], [(0.0, 1.0), (1.0, 2.0)], "あい", [True])
    assert all(placement.red for placement in done[0])
