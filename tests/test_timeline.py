# SPDX-License-Identifier: AGPL-3.0-only
"""Checks for the `.krc` timeline: the token each sound gets, and what it refuses to align."""

from __future__ import annotations

from typing import NamedTuple

import pytest

from namioto.karaoke import (
    KrcError,
    align_tokens,
    assign_by_order,
    assign_by_time,
    conflicts,
    contiguous,
    export_krc,
    group_sounds,
    map_faithful,
    map_sounds,
    note_counts,
    snap_to_beats,
    sound_lines,
    sound_ok,
    split,
    with_counts,
)
from namioto.karaoke.model import calc_mora
from namioto.utils import kana_tokens


def _tokens(text):
    return align_tokens(sound_lines(text))


def _shape(text):
    return [(sound.base, sound.ruby, sound.token) for line in sound_lines(text) for sound in line.sounds]


def test_plain_kana_is_one_token_per_sound():
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


def test_a_sokuon_or_long_vowel_with_nothing_to_lean_on_stands_alone():
    assert _tokens("コガネムシはあこがれさっ")[-2:] == ["sa", "'"]
    assert _tokens("んー") == ["n", "n"]
    assert _tokens("ーあ") == ["-", "a"]
    assert _tokens("あっっ") == ["a", "'", "'"]


def test_a_leading_small_kana_is_not_dropped():
    assert _tokens("ゃあ") == ["ya", "a"]


def test_a_small_kana_stays_with_the_sound_before_it():
    assert _tokens("きょう") == ["kyo", "u"]
    assert _tokens("ヴァイオリン") == ["va", "i", "o", "ri", "n"]


def test_a_ruby_is_cut_into_one_token_per_sound():
    assert _shape("季節[き,せつ]は") == [
        ("季", "き", "ki"),
        ("節", "せ", "se"),
        ("節", "つ", "tsu"),
        ("は", "は", "ha"),
    ]
    assert _tokens("明日[あした]を") == ["a", "shi", "ta", "o"]


def test_a_label_is_the_word_itself_without_a_ruby():
    assert [sound.label for line in sound_lines("だから") for sound in line.sounds] == ["だ", "か", "ら"]
    assert [sound.label for line in sound_lines("hello") for sound in line.sounds] == ["hello"]
    # the small kana stays one sound with the kana before it, and the block reads the whole surface
    assert [sound.label for line in sound_lines("きょう") for sound in line.sounds] == ["きょ", "う"]
    assert [sound.label for line in sound_lines("ワイドショー") for sound in line.sounds] == [
        "ワ",
        "イ",
        "ド",
        "ショ",
        "ー",
    ]


def test_a_label_puts_the_ruby_beside_the_base_unit_it_reads():
    # a comma ruby reads one kanji per part: the word opens with `()` and the rest wear `[]`
    assert [sound.label for line in sound_lines("世界[せ,かい]") for sound in line.sounds] == [
        "せ(世)",
        "か[界]",
        "い[界]",
    ]
    # one ruby for the whole run: the kanji is indivisible, so every sound of it wears `()`
    assert [sound.label for line in sound_lines("百合[ゆり]") for sound in line.sounds] == ["ゆ(百合)", "り(百合)"]


def test_a_latin_run_is_one_sound_and_a_digit_is_its_own():
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
    lines = sound_lines("あ\n---\n{2}い")
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
    lines = sound_lines(text)
    kana = "".join(sound.ruby for line in lines for sound in line.sounds)
    assert "".join(align_tokens(lines)) == "".join(kana_tokens(kana))


def test_a_kanji_without_a_ruby_is_refused():
    with pytest.raises(KrcError):
        sound_lines("世界")


class Token(NamedTuple):
    start: float | None
    end: float | None


def test_split_cuts_the_flat_tokens_into_lines():
    lines = sound_lines("あい\nうえ")
    tokens = [Token(0.0, 0.1), Token(0.1, 0.2), Token(0.5, 0.6), Token(0.6, 0.7)]
    assert split(tokens, lines) == [[(0.0, 0.1), (0.1, 0.2)], [(0.5, 0.6), (0.6, 0.7)]]


def test_snap_to_beats_rounds_every_boundary_onto_the_grid():
    # 120 BPM is half a second a beat
    assert snap_to_beats([[(0.1, 0.6), (0.6, 1.1)]], 120.0) == [[(0.0, 0.5), (0.5, 1.0)]]


def test_snap_to_beats_rounds_onto_the_offset_grid():
    assert snap_to_beats([[(0.1, 0.6), (0.6, 1.1)]], 120.0, 1.0, 0.25) == [[(0.25, 0.75), (0.75, 1.25)]]


def test_snap_to_beats_gives_a_sound_that_rounds_to_one_cell_no_length():
    # both ends of the first sound fall in the 0.0 cell, so it comes back with no length
    assert snap_to_beats([[(0.05, 0.2), (0.2, 0.3)]], 120.0) == [[(0.0, 0.0), (0.0, 0.5)]]


def test_snap_to_beats_leaves_an_unaligned_line_alone():
    row = [(None, None), (1.0, 2.0)]
    assert snap_to_beats([row], 120.0) == [[(None, None), (1.0, 2.0)]]


def test_a_sound_spanning_several_notes_counts_them():
    lines = sound_lines("あん")
    times = [[(0.0, 1.0), (1.0, 8.0)]]
    assert note_counts(lines, times, [(0.0, 1.0), (1.0, 4.0), (4.0, 8.0)]) == [[1, 2]]


def test_conflicts_report_an_unaligned_sound_and_a_stray_note():
    assert conflicts(sound_lines("あ"), [[(None, None)]], [(0.0, 1.0)]) == ["あ: not aligned"]
    assert conflicts(sound_lines("あ"), [[(0.0, 1.0)]], []) == ["あ: no note between 0.000 and 1.000"]


def test_conflicts_report_a_sound_that_does_not_sit_on_its_notes():
    problems = conflicts(sound_lines("あ"), [[(0.0, 1.0)]], [(0.2, 1.0)])
    assert problems == ["あ: does not start and end on its notes"]


def test_conflicts_report_a_shared_note():
    problems = conflicts(sound_lines("あい"), [[(0.0, 1.0), (0.0, 1.0)]], [(0.0, 1.0)])
    assert any("shares a note" in problem for problem in problems)


def test_sound_ok_marks_each_sound_that_sits_on_its_own_notes():
    lines = sound_lines("あん")
    notes = [(0.0, 1.0), (1.0, 2.0), (2.0, 3.0)]
    assert sound_ok(lines, [[(0.0, 1.0), (1.0, 3.0)]], notes) == [[True, True]]
    assert sound_ok(lines, [[(0.0, 1.0), (1.0, 3.0)]], [(0.0, 1.0)]) == [[True, False]]
    assert sound_ok(lines, [[(None, None), (1.0, 3.0)]], notes) == [[False, True]]
    assert sound_ok(lines, [[(0.2, 1.0), (1.0, 3.0)]], notes) == [[False, True]]
    assert sound_ok(lines, [[(0.0, 1.0), (0.0, 1.0)]], [(0.0, 1.0)]) == [[False, False]]
    assert sound_ok(lines, [[(0.0, 1.0), (1.0, 1.0)]], [(0.0, 1.0)]) == [[True, True]]  # no length, nothing to sit on


def test_a_sound_of_no_length_is_not_a_conflict():
    assert conflicts(sound_lines("あん"), [[(0.0, 1.0), (1.0, 1.0)]], [(0.0, 1.0)]) == []


def test_notes_line_up_within_the_tolerance():
    lines = sound_lines("あ")
    times = [[(0.0, 1.0)]]
    notes = [(0.03, 1.0)]
    assert conflicts(lines, times, notes) == []
    assert note_counts(lines, times, notes) == [[1]]


def test_with_counts_writes_a_held_sound_as_dot_n_and_drops_a_stale_one():
    assert with_counts("あん", [[1, 2]]) == "あん.2"
    assert with_counts("あん.3", [[1, 1]]) == "あん"


def test_with_counts_writes_inside_a_ruby_per_sound():
    assert with_counts("季節[き,せつ]", [[1, 2, 1]]) == "季節[き,せ.2つ]"


def test_export_krc_writes_a_held_sound_as_dot_n():
    assert export_krc("あ", sound_lines("あ"), [[(0.0, 2.0)]], [(0.0, 1.0), (1.0, 2.0)]) == "あ.2"


def test_export_krc_folds_a_shared_note_and_writes_its_dot_n():
    assert export_krc("あい", sound_lines("あい"), [[(0.0, 0.5), (0.5, 1.0)]], [(0.0, 1.0)]) == "(あい).1"


def test_export_krc_writes_a_sound_that_covers_no_note_as_dot_zero():
    assert export_krc("あい", sound_lines("あい"), [[(0.0, 1.0), (3.0, 3.5)]], [(0.0, 1.0)]) == "あい.0"


def test_export_krc_keeps_a_one_to_one_mapping_as_it_was():
    text = "季節[き,せつ]は"
    times = [[(0.0, 1.0), (1.0, 2.0), (2.0, 3.0), (3.0, 4.0)]]
    notes = [(0.0, 1.0), (1.0, 2.0), (2.0, 3.0), (3.0, 4.0)]
    assert export_krc(text, sound_lines(text), times, notes) == text


def test_export_krc_leaves_the_text_when_there_are_no_notes():
    assert export_krc("あい", sound_lines("あい"), [[(0.0, 1.0), (1.0, 2.0)]], []) == "あい"


def test_export_krc_reads_back_its_own_sounds():
    text = "コーヒー"
    times = [[(0.0, 0.5), (0.5, 1.0), (1.0, 1.5), (1.5, 2.0)]]
    notes = [(0.0, 1.0), (1.0, 2.0)]
    out = export_krc(text, sound_lines(text), times, notes)
    assert out == "(コー).1(ヒー).1"
    assert [sound.token for sound in sound_lines(out)[0].sounds] == ["ko", "o", "hi", "i"]


def test_grouping_a_sound_inside_a_group_that_holds_a_symbol_is_refused():
    # ・ carries no mora: dissolving (ブ・ユー) to fold inside it would take that sound away
    with pytest.raises(KrcError):
        group_sounds("みんな　アイ・ラ(ブ・ユー)", 0, 8, 9)


def test_export_krc_moves_a_zero_reading_onto_its_ruby_word():
    text = "遊[あそ]んで"
    times = [[(5.0, 5.2), (5.2, 5.4), (0.0, 0.5), (0.5, 1.0)]]
    notes = [(0.0, 0.5), (0.5, 1.0)]
    out = export_krc(text, sound_lines(text), times, notes)
    assert out == "遊[あそ].0んで"
    assert align_tokens(sound_lines(out)) == align_tokens(sound_lines(text))


def test_a_group_of_sounds_becomes_one_word_that_keeps_its_sounds():
    # コー is two sounds of one word; folding the run must not move the token stream
    text = group_sounds("コーヒー", 0, 0, 1)
    text = group_sounds(text, 0, 2, 3)
    assert text == "(コー)(ヒー)"
    # a `(...)` reads back as its members: same sounds, same tokens as the ungrouped run
    assert [sound.token for sound in sound_lines(text)[0].sounds] == ["ko", "o", "hi", "i"]
    assert [sound.token for sound in sound_lines("コーヒー")[0].sounds] == ["ko", "o", "hi", "i"]


def test_a_group_steps_over_a_small_kana_that_is_no_sound_of_its_own():
    # ショ is one sound of the two kana シ and ョ, so grouping it with ー takes both written words
    text = group_sounds("ワイドショー", 0, 3, 4)
    assert text == "ワイド(ショー)"
    assert [sound.token for sound in sound_lines(text)[0].sounds] == ["wa", "i", "do", "sho", "o"]


def test_a_group_folds_inside_one_ruby_part():
    # しょ and う are one ruby part, so they fold into `(しょう)` inside it
    text = group_sounds("胡椒[こ,しょう]", 0, 1, 2)
    assert text == "胡椒[こ,(しょう)]"
    assert [sound.token for sound in sound_lines(text)[0].sounds] == ["ko", "sho", "u"]


def test_a_group_dissolves_a_group_it_starts_inside():
    # い and う are mid two existing words, so both dissolve and the new word re-forms
    assert group_sounds("(あい)(うえ)", 0, 1, 2) == "あ(いう)え"


def test_grouping_refuses_a_crossing_run_and_a_lone_sound():
    with pytest.raises(KrcError):
        group_sounds("世界[せ,かい]", 0, 0, 1)  # across two ruby parts
    with pytest.raises(KrcError):
        group_sounds("胡椒[こ,しょう]は", 0, 2, 3)  # a ruby word and a plain one
    with pytest.raises(KrcError):
        group_sounds("あい", 0, 0, 0)  # one sound is no group


def test_faithful_holds_a_word_with_more_notes_than_sounds():
    # あ.2: one sound over two notes, from the first note's start to the second's end
    found = map_faithful("あ.2", [(0.0, 1.0), (1.0, 2.0)])
    assert [placement.span for placement in found[0]] == [(0.0, 2.0)]
    assert found[0][0].notes == (0, 1)


def test_faithful_shares_a_note_between_sounds():
    # (あい).1: two sounds on one note, each half of it, a group
    found = map_faithful("(あい).1", [(0.0, 1.0)])
    assert [placement.span for placement in found[0]] == [(0.0, 0.5), (0.5, 1.0)]
    assert [placement.notes for placement in found[0]] == [(0,), (0,)]
    assert [placement.group for placement in found[0]] == [0, 0]


def test_faithful_stops_when_the_notes_run_out():
    found = map_faithful("あいう", [(0.0, 1.0)])
    assert found[0][0].span == (0.0, 1.0)
    assert found[0][0].notes == (0,)
    assert found[0][1].zero is True
    assert found[0][1].span == (1.0, 1.0)  # a point at the end the notes ran out on
    assert found[0][2].zero is True


def test_faithful_puts_a_dot_n_sound_where_it_holds_off():
    # あい.0う: the `.0` sound takes no note, so it is a point at the note the next sound begins
    # on, not (None, None) - the strip has to draw its `|` and label there
    found = map_faithful("あい.0う", [(0.0, 1.0), (1.0, 2.0)])
    assert found[0][1].zero is True
    assert found[0][1].span == (1.0, 1.0)
    assert found[0][2].span == (1.0, 2.0)


def test_faithful_reads_a_ruby_part_by_part():
    # 確信犯[かく,(しん).1,はん]: five notes for six sounds, so the `.1` group gets one note of
    # its own and its two sounds share that third slot - not the whole span spread over time
    text = "確信犯[かく,(しん).1,はん]"
    found = map_faithful(text, [(i * 1.0, i * 1.0 + 1.0) for i in range(5)])
    assert [placement.span for placement in found[0]] == [
        (0.0, 1.0),
        (1.0, 2.0),
        (2.0, 2.5),
        (2.5, 3.0),
        (3.0, 4.0),
        (4.0, 5.0),
    ]
    assert [placement.notes for placement in found[0]] == [(0,), (1,), (2,), (2,), (3,), (4,)]
    assert [placement.group for placement in found[0]] == [-1, -1, 2, 2, -1, -1]


def test_faithful_confines_a_shared_sound_to_its_note():
    # (あいう).2: three sounds over two notes, so two share the second note and none reaches into
    # the rest between the notes
    found = map_faithful("(あいう).2", [(0.0, 1.0), (2.0, 3.0)])
    assert [placement.span for placement in found[0]] == [(0.0, 1.0), (2.0, 2.5), (2.5, 3.0)]
    assert [placement.notes for placement in found[0]] == [(0,), (1,), (1,)]


@pytest.mark.parametrize("text", ["あん", "季節[き,せつ]", "がっこう", "(幾千)[いくせん]"])
def test_with_counts_leaves_the_readings_alone(text):
    counts = [[1] * len(line.sounds) for line in sound_lines(text)]
    again = sound_lines(with_counts(text, counts))
    assert [sound.ruby for line in again for sound in line.sounds] == [
        sound.ruby for line in sound_lines(text) for sound in line.sounds
    ]


def test_a_group_reads_back_its_own_sounds():
    assert [sound.label for sound in sound_lines("(あい)")[0].sounds] == ["あ", "い"]
    assert [sound.token for sound in sound_lines("(あい)")[0].sounds] == ["a", "i"]
    # a group inside a ruby reads back as its members, with the same token stream as the plain run
    grouped = sound_lines("胡椒[こ,(しょう)]")[0].sounds
    plain = sound_lines("胡椒[こ,しょう]")[0].sounds
    assert [sound.token for sound in grouped] == [sound.token for sound in plain] == ["ko", "sho", "u"]


def test_rubied_sounds_that_share_a_note_group_on_it():
    # grouping is display-only: two rubied sounds each half of one note are still a group
    found = map_sounds(sound_lines("め[し]椒[う]"), [[(0.0, 1.0), (1.0, 2.0)]], [(0.0, 2.0)], "め[し]椒[う]")
    assert [placement.notes for placement in found[0]] == [(0,), (0,)]
    assert [placement.group for placement in found[0]] == [0, 0]


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


def test_contiguous_takes_each_sound_end_from_the_next_start():
    assert contiguous([[(0.0, 0.9), (0.9, 0.95), (0.95, 1.0)]]) == [[(0.0, 0.9), (0.9, 0.95), (0.95, 1.0)]]
    # the aligner's own end is dropped: the second sound's start is the first sound's end
    assert contiguous([[(0.0, 1.0), (2.0, 3.0)]]) == [[(0.0, 2.0), (2.0, 3.0)]]


def test_without_alignment_the_sounds_and_notes_pair_one_for_one():
    found = map_sounds(sound_lines("あいう"), [[(None, None)] * 3], [(0.0, 1.0), (1.0, 2.0)], aligned=False)
    assert [placement.span for placement in found[0]] == [(0.0, 1.0), (1.0, 2.0), (None, None)]
    assert [placement.notes for placement in found[0]] == [(0,), (1,), ()]


def test_a_sound_covers_the_note_its_time_overlaps():
    found = map_sounds(sound_lines("あい"), [[(0.0, 1.0), (1.0, 2.0)]], [(0.0, 1.0), (1.0, 2.0)], "あい")
    assert [placement.notes for placement in found[0]] == [(0,), (1,)]
    assert [placement.span for placement in found[0]] == [(0.0, 1.0), (1.0, 2.0)]
    assert not any(placement.red for placement in found[0])


def test_a_long_sound_covers_every_note_it_overlaps():
    notes = [(0.0, 1.0), (1.0, 2.0), (2.0, 3.0)]
    found = map_sounds(sound_lines("あい"), [[(0.0, 2.0), (2.0, 3.0)]], notes, "あい")
    assert found[0][0].notes == (0, 1)
    assert found[0][0].span == (0.0, 2.0)
    assert not found[0][0].red
    assert found[0][1].notes == (2,)


def test_two_sounds_that_share_a_note_equally_group_on_it():
    times = [[(0.0, 1.0), (1.0, 2.0)]]
    found = map_sounds(sound_lines("あい"), times, [(0.0, 2.0)], "あい")
    assert [placement.notes for placement in found[0]] == [(0,), (0,)]
    assert [placement.span for placement in found[0]] == [(0.0, 1.0), (1.0, 2.0)]
    assert [placement.group for placement in found[0]] == [0, 0]


def test_a_note_split_a_quarter_to_three_quarters_still_groups():
    # the reported case: the boundary may sit anywhere between 1/4 and 3/4 of the note
    found = map_sounds(sound_lines("あい"), [[(0.0, 0.25), (0.25, 1.0)]], [(0.0, 1.0)], "あい")
    assert [placement.notes for placement in found[0]] == [(0,), (0,)]
    assert [placement.group for placement in found[0]] == [0, 0]


def test_a_note_split_past_a_quarter_falls_to_no_length():
    found = map_sounds(sound_lines("あい"), [[(0.0, 0.24), (0.24, 1.0)]], [(0.0, 1.0)], "あい")
    assert found[0][0].zero is True
    assert found[0][1].notes == (0,)
    assert [placement.group for placement in found[0]] == [-1, -1]


def test_a_weak_sharer_does_not_drag_down_the_others():
    # あ 44%, い 39%, う 9% on one note: only う falls to no length, never い
    times = [[(0.2, 1.2), (1.2, 2.1), (2.1, 2.3)]]
    found = map_sounds(sound_lines("あいう"), times, [(0.0, 2.3)], "あいう")
    assert [placement.notes for placement in found[0]] == [(0,), (0,), ()]
    assert [placement.zero for placement in found[0]] == [False, False, True]
    assert [placement.group for placement in found[0]] == [0, 0, -1]


def test_four_sounds_each_holding_a_quarter_all_keep_the_note():
    times = [[(0.0, 0.5), (0.5, 1.0), (1.0, 1.5), (1.5, 2.0)]]
    found = map_sounds(sound_lines("あいうえ"), times, [(0.0, 2.0)], "あいうえ")
    assert [placement.notes for placement in found[0]] == [(0,)] * 4
    assert all(not placement.zero for placement in found[0])
    assert [placement.group for placement in found[0]] == [0, 0, 0, 0]


def test_four_sounds_under_a_quarter_fall_to_no_length():
    # 40/25/20/15: only the two that hold a quarter keep the note
    times = [[(0.0, 0.8), (0.8, 1.3), (1.3, 1.7), (1.7, 2.0)]]
    found = map_sounds(sound_lines("あいうえ"), times, [(0.0, 2.0)], "あいうえ")
    assert [placement.zero for placement in found[0]] == [False, False, True, True]
    assert [placement.group for placement in found[0]] == [0, 0, -1, -1]


def test_the_smaller_share_of_a_note_falls_to_no_length():
    times = [[(0.0, 1.8), (1.8, 2.0)]]
    found = map_sounds(sound_lines("あい"), times, [(0.0, 2.0)], "あい")
    assert found[0][0].notes == (0,)
    assert found[0][1].zero is True
    assert found[0][1].span == (1.8, 1.8)
    assert [placement.group for placement in found[0]] == [-1, -1]


def test_a_note_no_sound_reaches_is_given_to_the_one_before_and_doubted():
    notes = [(0.0, 1.0), (1.0, 2.0), (2.0, 3.0)]
    found = map_sounds(sound_lines("あい"), [[(0.0, 1.0), (2.0, 3.0)]], notes, "あい")
    assert found[0][0].notes == (0, 1)
    assert found[0][0].red is True
    assert found[0][1].notes == (2,)


def test_a_note_before_the_first_sound_is_given_to_it_and_doubted():
    done = map_sounds(sound_lines("あ"), [[(1.0, 2.0)]], [(0.0, 1.0), (1.0, 2.0)], "あ")
    assert done[0][0].notes == (0, 1)
    assert done[0][0].span == (0.0, 2.0)
    assert done[0][0].red is True


def test_a_note_after_the_last_sound_is_given_to_it_and_doubted():
    done = map_sounds(sound_lines("あ"), [[(0.0, 1.0)]], [(0.0, 1.0), (1.0, 2.0)], "あ")
    assert done[0][0].notes == (0, 1)
    assert done[0][0].span == (0.0, 2.0)
    assert done[0][0].red is True


def test_a_sound_that_covers_no_note_falls_to_no_length_without_doubt():
    done = map_sounds(sound_lines("あい"), [[(0.0, 1.0), (3.0, 3.5)]], [(0.0, 1.0)], "あい")
    assert done[0][0].notes == (0,)
    assert done[0][1].zero is True
    assert done[0][1].red is False
    assert done[0][1].group == -1


def test_a_flagged_line_is_doubted_whole():
    done = map_sounds(sound_lines("あい"), [[(0.0, 1.0), (1.0, 2.0)]], [(0.0, 1.0), (1.0, 2.0)], "あい", [True])
    assert all(placement.red for placement in done[0])
