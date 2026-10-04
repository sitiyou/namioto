# SPDX-License-Identifier: AGPL-3.0-only
"""Checks for the ASS subtitle: its header, its `\\k` stream, its gaps, dots and settings."""

from __future__ import annotations

import pytest

from namioto.karaoke import AssSettings, generate_ass


def _dialogues(text, times, **kwargs):
    return [line for line in generate_ass(text, times, **kwargs).splitlines() if line.startswith("Dialogue:")]


def test_the_header_carries_the_styles_and_one_events_section():
    ass = generate_ass("あ", [[(0.0, 0.5)]])
    assert "[Script Info]" in ass
    assert "[V4+ Styles]" in ass
    assert ass.count("[Events]") == 1
    for style in ("Style: K1,", "Style: K2,", "Style: LEAD,", "Style: H1,", "Style: H2,"):
        assert style in ass
    assert "template syl" in ass
    assert "template furi" in ass


def test_a_ruby_word_writes_its_base_and_readings():
    line = _dialogues("青[あお]", [[(0.0, 0.5), (0.5, 1.0)]])[0]
    assert "\\k50}青|<あ" in line
    assert "\\k50}#|お" in line


def test_a_rest_between_sounds_writes_a_gap():
    line = _dialogues("あい", [[(0.0, 0.5), (0.8, 1.0)]])[0]
    assert "\\k50}あ" in line
    assert "{\\k30}{\\k20}い" in line


def test_a_small_kana_stays_one_sound_with_the_sound_before_it():
    # 京[きょう] reads きょ and う - one span each, never き + ょ
    line = _dialogues("京[きょう]", [[(0.0, 1.0), (1.0, 2.0)]])[0]
    assert "\\k100}京|<きょ" in line
    assert "\\k100}#|う" in line


def test_the_times_are_written_as_the_format_counts_them():
    line = _dialogues("あ", [[(3661.23, 3661.5)]], settings=AssSettings(lead_time_ms=0))[0]
    assert "1:01:01.23" in line


def test_a_chapter_opens_with_three_guide_dots():
    dialogues = _dialogues("あ", [[(5.0, 5.5)]])
    assert dialogues[0].startswith("Dialogue: 0,")
    assert ",LEAD,,0,0,0,,{\\k100}●{\\k100}●{\\k100}●" in dialogues[0]
    assert dialogues[1].split(",", 4)[3] == "K1"


def test_a_first_word_too_soon_gets_no_dots():
    dialogues = _dialogues("あ", [[(1.0, 1.5)]])
    assert len(dialogues) == 1  # the line itself, no LEAD dot row


def test_an_input_override_does_not_change_the_natural_sounds():
    spans = [(0.0, 0.25), (0.25, 0.5), (0.5, 0.75), (0.75, 1.0), (1.0, 1.5)]
    line = _dialogues("(幾千)[いくせん].1あ", [spans])[0]
    assert "\\k25}幾千|<い" in line
    assert "\\k25}#|く" in line
    assert "\\k25}#|せ" in line
    assert "\\k25}#|ん" in line
    assert "\\k50}あ" in line


def test_a_ruby_of_even_morae_gets_one_glyph_per_sound():
    # 胡椒[こ,しょう] is three sounds, not four: the old mora walk split しょ
    line = _dialogues("胡椒[こ,しょう]", [[(0.0, 0.5), (0.5, 1.0), (1.0, 1.5)]])[0]
    assert "\\k50}胡|<こ" in line
    assert "\\k50}椒|しょ" in line
    assert "\\k50}#|う" in line


def test_a_merge_tiles_its_note_across_its_sounds():
    line = _dialogues("(あい).1", [[(0.0, 0.5), (0.5, 1.0)]])[0]
    assert "\\k50}あ" in line
    assert "\\k50}い" in line


def test_a_cross_container_merge_keeps_the_ruby_and_tiles_the_note():
    line = _dialogues("泣[な]い.+", [[(0.0, 0.5), (0.5, 1.0)]])[0]
    assert "\\k50}泣|<な" in line
    assert "\\k50}い" in line


def test_the_settings_name_the_font_and_the_overlay():
    ass = generate_ass("あ", [[(0.0, 0.5)]], settings=AssSettings(font="Test Font", overlay_color="FF0000"))
    assert "Style: K1,Test Font,96," in ass
    assert r"\1c&H0000FF&" in ass  # RGB red is written in the ASS order


def test_an_unknown_track_style_is_refused():
    with pytest.raises(ValueError, match="track style"):
        generate_ass("あ", [[(0.0, 0.5)]], settings=AssSettings(track_style={1: "nowhere"}))


def test_a_line_with_no_time_is_left_out():
    ass = generate_ass("あ", [[(None, None)]])
    assert "[Events]" in ass
    assert "Dialogue:" not in ass
