# SPDX-License-Identifier: AGPL-3.0-only
"""Read a saved project without an audio player, MIDI round-trip, or rendering dependency."""

import pytest

from namioto import export_cli, project
from namioto.karaoke.song_input import load_project_input


def test_project_input_preserves_timing_readings_and_channels(tmp_path):
    path = tmp_path / "song.nto"
    settings = project.default_settings()
    settings.tempo.bpm = 137
    settings.lyric_colors.channel_2 = "#ABCDEF"
    project.save(
        project.Project(
            audio="missing-recording.wav",
            lyrics=project.Lyrics(text="京[@c2{きょう}]\n---\n@1{あ}"),
            notes=(project.Note(4.0001, 0.6, 60, 0), project.Note(4.8, 1, 62, 0), project.Note(7, 1, 64, 1)),
            settings=settings,
        ),
        path,
    )

    song = load_project_input(path)
    first, harmony = song.lines
    assert song.spans[0][0] == pytest.approx((4.0001, 4.6001))
    assert song.spans[0][1] == pytest.approx((4.8, 5.8))
    assert song.spans[1] == ((7.0, 8.0),)
    assert (first.text, first.track, first.chapter) == ("京", 0, 0)
    assert [(sound.reading, sound.color_channel) for sound in first.sounds] == [("きょ", 2), ("う", 2)]
    assert (harmony.text, harmony.track, harmony.chapter) == ("あ", 1, 1)
    assert song.bpm == 137
    assert song.channel_colors[2] == "ABCDEF"
    assert list(tmp_path.iterdir()) == [path]

    target = tmp_path / "song.ass"
    assert export_cli.main(["--project", str(path), "--out", str(target), "--offset-ms", "-100"]) == 0
    assert "Dialogue: 0,0:00:03.90,0:00:05.70,Default,,0,0,0,,京" in target.read_text()
    assert "Dialogue: 0,0:00:06.90,0:00:07.90,Default,,0,0,0,,あ" in target.read_text()
