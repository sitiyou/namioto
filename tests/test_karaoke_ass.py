# SPDX-License-Identifier: AGPL-3.0-only
"""Final ASS events, structured timing, ruby layout and export settings."""

import re

import pytest
from kara_templater import Document

from namioto.karaoke import ExportSettings, generate_ass
from namioto.karaoke.default_template import default_scene
from namioto.karaoke.song import _convert, prepare_song
from namioto.karaoke.sounds import natural_sounds


def _source(text, spans, settings=None):
    return _convert(natural_sounds(text)[0], spans, settings or ExportSettings(), 0)


def _events(text, spans, settings=None):
    return Document.parse(generate_ass(text, spans, settings=settings)).lines


def _surface(event):
    return re.sub(r"\{[^}]*\}", "", event.text)


def test_untimed_leading_ruby_does_not_pull_line_start_to_zero():
    scene = default_scene(prepare_song("想[お.0も]い", [[None, (62, 62.3), (62.4, 63)]], ExportSettings()))
    assert scene.plans[0].input.singing.start >= 62
    assert scene.plans[0].placement.display.start >= 57


def test_an_entirely_untimed_ruby_line_is_omitted():
    assert not _events("青[あお]", [[None, None]])


def test_final_output_has_styles_but_no_templates_or_timing_protocol():
    result = generate_ass("あ", [[(0, 0.5)]])
    assert result.count("[Events]") == 1
    assert Document.parse(result).lines
    assert "template syl" not in result


@pytest.mark.parametrize("spacing", [5, 0, -2.5, 7.5])
def test_font_spacing_reaches_every_style(spacing):
    doc = Document.parse(generate_ass("青[あお]", [[(0, 0.5), (0.5, 1)]], settings=ExportSettings(spacing=spacing)))
    assert all(doc.styles[s].spacing == spacing for s in ("default", "top", "default-furigana", "top-furigana"))


@pytest.mark.parametrize("text,duration", [("abc", 1), ("abc", 0.02), ("きょ", 1.01), ("abc[あ]", 1)])
def test_multicharacter_sound_retains_its_exact_time(text, duration):
    unit = _source(text, [(0, duration)]).units[0]
    assert unit.text == ("abc" if text.startswith("abc") else "きょ")
    assert [(s.start_ms, s.end_ms) for s in unit.segments] == [(0, round(duration * 1000))]


@pytest.mark.parametrize("text,expected", [("青[あお]", ["あ", "お"]), ("京[きょう]", ["きょ", "う"])])
def test_one_base_has_separate_ruby_times(text, expected):
    song = prepare_song(text, [[(0, 1), (1.5, 2)]], ExportSettings())
    (unit,) = song.sources[0].lyrics.units
    assert [s.ruby.source.text if s.ruby else None for s in unit.segments] == expected
    assert [(s.singing.start, s.singing.end) for s in unit.segments] == [(0, 1), (1.5, 2)]


def test_shared_base_overlap_is_rejected_with_line_and_surface():
    with pytest.raises(ValueError, match="lyric line 1:.*青"):
        generate_ass("青[あお]", [[(0, 2), (1, 3)]])


def test_zero_duration_borrows_time_without_removing_the_rest():
    song = prepare_song("あい", [[(0, 0.5), (0.8, 0.8)]], ExportSettings())
    assert [(s.singing.start, s.singing.end) for u in song.sources[0].lyrics.units for s in u.segments] == [
        (0, 0.42),
        (0.72, 0.8),
    ]


@pytest.mark.parametrize(
    "text,spans,expected",
    [
        ("あい.0", [(0, 1), (1, 1)], [(0, 920), (920, 1000)]),
        ("あ.0い", [(0, 0), (0, 1)], [(0, 80), (80, 1000)]),
        ("あ.0", [(1, 1)], [(1000, 1010)]),
        ("@0{あ}@1{い.0}", [(0, 1), (1, 1)], [(0, 1000), (1000, 1010)]),
        ("青[あお]", [(0, 1), (1, 1)], [(0, 920), (920, 1000)]),
        ("あ@c1{い.0}", [(0, 1), (1, 1)], [(0, 920), (920, 1000)]),
    ],
)
def test_borrowed_display_times(text, spans, expected):
    original = list(spans)
    source = _source(text, spans)
    assert [(s.start_ms, s.end_ms) for u in source.units for s in u.segments] == expected
    assert spans == original


def test_display_order_can_differ_from_time_order():
    song = prepare_song("あい", [[(2, 3), (0, 1)]], ExportSettings())
    assert song.sources[0].text == "あい"
    assert [u.segments[0].singing.start for u in song.sources[0].lyrics.units] == [2, 0]
    assert default_scene(song).plans[0].placement.display.end == pytest.approx(3.2)


def test_excess_simultaneous_rows_fail_instead_of_rendering_offscreen():
    with pytest.raises(ValueError, match="does not fit vertically"):
        generate_ass("\n".join("あ" for _ in range(8)), [[(0, 3)]] * 8)


def test_times_use_ass_centiseconds():
    settings = ExportSettings(lead_time_ms=0, fade_in_ms=0, cue="off")
    near = _events("あ", [[(1, 1.27)]], settings)
    far = _events("あ", [[(3661.23, 3661.5)]], settings)
    assert min(event.start_time for event in far) - min(event.start_time for event in near) == 3660230


def test_guide_dots_are_positioned_independent_events():
    scene = default_scene(prepare_song("あ", [[(5, 5.5)]], ExportSettings()))
    (cue,) = scene.lines[0].independent
    assert cue.lifetime.start == 2 and cue.lifetime.end == 5


def test_first_word_too_soon_gets_no_dots():
    assert not prepare_song("あ", [[(1, 1.5)]], ExportSettings()).sources[0].lyrics.body.cues


def test_mapping_override_does_not_change_natural_sounds():
    source = _source("(幾千)[いくせん].1あ", [(0, 0.25), (0.25, 0.5), (0.5, 0.75), (0.75, 1), (1, 1.5)])
    assert [u.text for u in source.units] == ["幾千", "あ"]
    assert [s.ruby for s in source.units[0].segments] == ["い", "く", "せ", "ん"]


@pytest.mark.parametrize("text", ["情熱[じょう,ねつ]", "行先[ゆく,さき]"])
def test_comma_separated_ruby_is_one_layout_group(text):
    song = prepare_song(text, [[(0, 0.5), (0.5, 1), (1, 1.5), (1.5, 2)]], ExportSettings())
    first, second = song.sources[0].lyrics.units
    assert first.segments[0].ruby is not None
    assert second.segments[0].ruby is not None
    assert first.segments[0].ruby.mode == "spill"
    assert second.segments[0].ruby.mode == "join"
    assert len(default_scene(song).lines[0].layout.ruby) == 4


def test_separate_ruby_words_keep_separate_layout_groups():
    source = _source("情[じょう]熱[ねつ]", [(0, 0.5), (0.5, 1), (1, 1.5), (1.5, 2)])
    assert all(not u.ruby_continuation for u in source.units)


def test_ruby_parts_have_distinct_bases():
    source = _source("胡椒[こ,しょう]", [(0, 0.5), (0.5, 1), (1, 1.5)])
    assert [u.text for u in source.units] == ["胡", "椒"]
    assert [[s.ruby for s in u.segments] for u in source.units] == [["こ"], ["しょ", "う"]]


@pytest.mark.parametrize("text", ["(あい).1", "泣[な]い.+"])
def test_merge_keeps_sound_times(text):
    source = _source(text, [(0, 0.5), (0.5, 1)])
    assert [(s.start_ms, s.end_ms) for u in source.units for s in u.segments] == [(0, 500), (500, 1000)]


def test_font_and_rgb_colour_reach_final_output():
    output = generate_ass("あ", [[(0, 0.5)]], settings=ExportSettings(font="Test Font", overlay_color="FF0000"))
    assert Document.parse(output).styles["default"].fontname == "Test Font"
    assert "0000FF&" in output


def test_unknown_track_style_is_refused():
    with pytest.raises(ValueError, match="track style"):
        generate_ass("あ", [[(0, 0.5)]], settings=ExportSettings(track_style={0: "nowhere"}))


def test_no_time_or_mismatched_spans_omits_line():
    assert not _events("あ", [[(None, None)]])
    assert not _events("あ", [[]])


def test_layout_and_effect_options_reach_final_events():
    settings = ExportSettings(
        font_size=72,
        blur=8,
        border=7,
        border_furi=4,
        base_outline_color="123456",
        overlay_outline_color="ABCDEF",
        overlay_blur_color="102030",
        base_blur_color="405060",
    )
    output = generate_ass("字[じ]", [[(5, 6)]], settings=settings)
    assert Document.parse(output).styles["default-furigana"].fontsize == 36
    for tag in (r"\bord7", r"\bord4", r"\blur8", "563412&", "EFCDAB&", "302010&", "605040&"):
        assert tag in output


def test_zero_borders_and_blur_are_supported():
    result = generate_ass(
        "字[じ]",
        [[(1, 2)]],
        settings=ExportSettings(
            border=0,
            border_furi=0,
            blur=0,
            blur_scale=0,
            ruby_offset=0,
        ),
    )
    assert r"\bord0" in result
    assert r"\blur0" in result
    assert "nan" not in result.lower()


def test_positive_offset_moves_lyrics_and_dots_without_mutating_spans():
    spans = [[(5, 5.5)]]
    scene = default_scene(prepare_song("あ", spans, ExportSettings(offset_ms=2000)))
    assert scene.plans[0].input.singing.start == 7
    assert scene.lines[0].independent[0].lifetime.start == 4
    assert spans == [[(5, 5.5)]]


def test_negative_offset_clips_elapsed_segments_without_borrowing():
    source = _source("青[あお]い", [(0, 1), (1, 2), (2.5, 3)], ExportSettings(offset_ms=-1500))
    assert [(s.start_ms, s.end_ms) for u in source.units for s in u.segments] == [(0, 0), (0, 500), (1000, 1500)]
    assert [s.ruby for s in source.units[0].segments] == ["あ", "お"]


def test_partial_syllable_keeps_only_time_after_zero():
    source = _source("あい", [(0, 1), (1, 2)], ExportSettings(offset_ms=-250))
    assert [(s.start_ms, s.end_ms) for u in source.units for s in u.segments] == [(0, 750), (750, 1750)]


def test_lines_fully_before_zero_are_omitted():
    events = _events("あ\nい", [[(0, 1)], [(3, 4)]], ExportSettings(offset_ms=-2000))
    assert {_surface(e) for e in events} == {"い"}


def test_auto_base_blur_follows_manual_overlay_blur():
    settings = ExportSettings(overlay_blur_color="123456")
    overlay, base = settings.blur_colours()
    assert overlay == "123456"
    assert base != ExportSettings().blur_colours()[1]
    result = generate_ass("あ", [[(0, 1)]], settings=settings)
    assert r"\3c&H563412&" in result
    assert rf"\3c&H{base[4:] + base[2:4] + base[:2]}&" in result
