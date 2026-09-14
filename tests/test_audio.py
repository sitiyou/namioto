# SPDX-License-Identifier: AGPL-3.0-only
"""Checks for the two note players: a preview overlaps what is already sounding."""

from __future__ import annotations

import time
from types import SimpleNamespace

import numpy as np

from namioto.playback import render_notes
from namioto.ui.audio import NOTE_OFF, NOTE_ON, VELOCITY, MidiPortOut, MidiSink


class FakePort:
    """Collects what a synth would have received."""

    def __init__(self) -> None:
        self.messages: list[list[int]] = []

    def send_message(self, message) -> None:
        self.messages.append(list(message))


def test_a_preview_mixes_over_what_is_already_sounding() -> None:
    voice = render_notes([(60, 0.0, 0.05)])
    sink = MidiSink()
    sink.mix = np.zeros(len(voice) * 2, dtype=np.float32)
    sink._source = SimpleNamespace(cursor=0)  # the stream is being read from the top

    sink.preview(60, 0.05)
    assert np.array_equal(sink.mix[: len(voice)], voice)

    sink.preview(67, 0.05)  # a second click before the first note has finished
    assert np.allclose(sink.mix[: len(voice)], voice + render_notes([(67, 0.0, 0.05)]))


def note_messages(port) -> list[list[int]]:
    """Just the note events: the player also opens with a channel volume control change."""
    return [message for message in port.messages if message[0] in (NOTE_ON, NOTE_OFF)]


def test_clicking_the_same_note_again_restarts_it() -> None:
    port = FakePort()
    player = MidiPortOut(port)

    player.preview(60, 0.15)
    player.preview(60, 0.15)  # the second click releases the note and starts it over
    time.sleep(0.3)

    assert note_messages(port) == [
        [NOTE_ON, 60, VELOCITY],
        [NOTE_OFF, 60, 0],  # a synth that still holds the note would only layer a second one
        [NOTE_ON, 60, VELOCITY],
        [NOTE_OFF, 60, 0],
    ]


def test_stopping_silences_a_preview() -> None:
    port = FakePort()
    player = MidiPortOut(port)

    player.preview(60, 30.0)
    player.stop()

    assert note_messages(port)[-1] == [NOTE_OFF, 60, 0]


def test_clicking_the_same_note_again_restarts_the_built_in_voice() -> None:
    voice = render_notes([(60, 0.0, 0.05)])
    sink = MidiSink()
    sink.mix = np.zeros(len(voice) * 2, dtype=np.float32)
    sink._source = SimpleNamespace(cursor=0)

    sink.preview(60, 0.05)
    assert np.array_equal(sink.mix[: len(voice)], voice)

    sink.preview(60, 0.05)  # the note is released and started again, not stacked on itself
    released = int(0.05 * sink.sample_rate)
    assert np.allclose(sink.mix[released : len(voice)], voice[released:])


def test_the_midi_volume_becomes_a_control_change() -> None:
    port = FakePort()
    player = MidiPortOut(port)

    assert port.messages[0] == [0xB0, 0x07, 127]  # the base class starts it at full volume
    player.gain = 0.5
    assert port.messages[-1] == [0xB0, 0x07, round(0.5 * 127)]
    player.gain = 2.0  # a synth clamps at its own top
    assert port.messages[-1] == [0xB0, 0x07, 127]
    player.gain = -1.0
    assert port.messages[-1] == [0xB0, 0x07, 0]
    assert player.gain == 0.0
