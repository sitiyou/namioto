# SPDX-License-Identifier: AGPL-3.0-only
"""The basic ASS exporter writes one event per timed lyric line."""

import pytest

from namioto.karaoke import ExportSettings, generate_ass


def dialogue(output):
    return [line.removeprefix("Dialogue: ").split(",", 9) for line in output.splitlines()
            if line.startswith("Dialogue: ")]


def test_one_default_style_and_one_event_per_timed_line():
    output = generate_ass("京[きょう]、\n---\n@1{あ}", [[(1.0, 1.5), (2.0, 2.4)], [(1.2, 1.9)]])
    assert output.count("\nStyle: ") == 1
    assert dialogue(output) == [
        ["0", "0:00:01.00", "0:00:02.40", "Default", "", "0", "0", "0", "", "京、"],
        ["0", "0:00:01.20", "0:00:01.90", "Default", "", "0", "0", "0", "", "あ"],
    ]


def test_untimed_lines_and_sounds_do_not_extend_the_subtitle():
    rows = dialogue(generate_ass("幸[し.0あ]せ\nあ.0", [[None, (1.2, 1.6), (2.0, 2.2)], [None]]))
    assert len(rows) == 1
    assert rows[0][1:3] == ["0:00:01.20", "0:00:02.20"]
    assert rows[0][-1] == "幸せ"


@pytest.mark.parametrize(("offset", "expected"), [
    (-350, [("0:00:00.00", "0:00:00.15", "い")]),
    (300, [("0:00:00.35", "0:00:00.50", "あ"), ("0:00:00.55", "0:00:00.80", "い")]),
])
def test_offset_clips_at_the_recording_start(offset, expected):
    rows = dialogue(generate_ass("あ\nい", [[(0.05, 0.2)], [(0.25, 0.5)]], settings=ExportSettings(offset)))
    assert [tuple(row[1:3] + row[-1:]) for row in rows] == expected


def test_ass_uses_centiseconds_and_keeps_short_lines_visible():
    row, = dialogue(generate_ass("あ", [[(3660.502, 3660.503)]]))
    assert row[1:3] == ["1:01:00.50", "1:01:00.51"]


def test_bad_intervals_are_rejected():
    with pytest.raises(ValueError, match="reversed"):
        generate_ass("あ", [[(2, 1)]])
    with pytest.raises(ValueError, match="match"):
        generate_ass("あい", [[(1, 2)]])
