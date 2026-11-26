# SPDX-License-Identifier: AGPL-3.0-only
"""Simultaneous channels retain their independent note clocks before ASS export."""

from namioto import midi, project, subtitle
from namioto.channels import Channel
from namioto.karaoke import generate_ass
from namioto.karaoke.song_input import build_song


def test_multiline_scope_does_not_share_note_cursors():
    text = "@1{あ}\n@1{い}\n@2{う}"
    notes = (
        project.Note(0, 1, 60, 1), project.Note(2, 1, 62, 1), project.Note(0, 3, 64, 2),
    )
    data = midi.to_bytes((Channel(channel=1), Channel(channel=2)), notes, 120)
    assert subtitle.build_ass(text, data) == generate_ass(text, [[(0, 1)], [(2, 3)], [(0, 3)]])


def test_inline_overlapping_channels_keep_exact_timing_and_colour_ownership():
    text = "@1{@c3{あ} }@2{い}"
    notes = (project.Note(0, 2, 60, 1), project.Note(1, 2, 62, 2))
    data = midi.to_bytes((Channel(channel=1), Channel(channel=2)), notes, 120)
    song = build_song(text, data)
    line, = song.lines
    assert line.text == "あ い" and line.track == -1
    assert [(sound.channel, sound.color_channel) for sound in line.sounds] == [(1, 3), (2, None)]
    assert song.spans == (((0.0, 2.0), (1.0, 3.0)),)
    assert "Dialogue: 0,0:00:00.00,0:00:03.00,Default,,0,0,0,,あ い" in subtitle.build_ass(text, data)
