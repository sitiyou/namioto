# SPDX-License-Identifier: AGPL-3.0-only
"""Checks for external MIDI playback and silent fallback."""

from __future__ import annotations

import sys
import threading
import time
import types

import pytest

from namioto.ui import audio
from namioto.ui.audio import (
    NOTE_OFF,
    NOTE_ON,
    PROGRAM_CHANGE,
    VELOCITY,
    MidiPortOut,
    SilentPlayer,
    open_player,
)


class FakePort:
    """Collects what a synth would have received."""

    def __init__(self) -> None:
        self.messages: list[list[int]] = []

    def send_message(self, message) -> None:
        self.messages.append(list(message))


def note_messages(port) -> list[list[int]]:
    """Just the note events on any channel: the player also sends program and volume changes."""
    return [message for message in port.messages if (message[0] & 0xF0) in (NOTE_ON, NOTE_OFF)]


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


def test_each_channel_gets_its_program_and_volume() -> None:
    port = FakePort()
    player = MidiPortOut(port)

    player.set_program([(60, 0.0, 1.0, 0), (43, 0.0, 1.0, 10)], 1.0, ((0, 0, 100), (10, 32, 50)))
    changes = [message for message in port.messages if (message[0] & 0xF0) == PROGRAM_CHANGE]
    volumes = [message for message in port.messages if (message[0] & 0xF0) == 0xB0 and message[1] == 7]
    assert [message[0] & 0x0F for message in changes] == [0, 10]
    assert [message[1] for message in changes] == [0, 32]
    assert [message[2] for message in volumes][-2:] == [127, round(0.5 * 127)]


def test_the_notes_are_played_on_their_own_channel() -> None:
    port = FakePort()
    player = MidiPortOut(port)
    player.set_program([(60, 0.0, 0.2, 3), (67, 0.0, 0.2, 0)], 1.0, ((0, 0, 100), (3, 0, 100)))
    port.messages.clear()
    player.play()
    time.sleep(0.3)
    player.stop()

    assert note_messages(port) == [
        [NOTE_ON | 3, 60, VELOCITY],
        [NOTE_ON | 0, 67, VELOCITY],
        [NOTE_OFF | 3, 60, 0],
        [NOTE_OFF | 0, 67, 0],
    ]


def test_two_note_ons_for_one_pitch_are_both_released_on_stop() -> None:
    port = FakePort()
    player = MidiPortOut(port)

    player.set_program([(69, 0.0, 1.0, 0), (69, 0.0, 1.0, 0)], 1.0, ((0, 0, 100),))
    port.messages.clear()
    player.play()
    time.sleep(0.1)  # both note-ons of the one pitch are sounding
    player.pause()

    assert note_messages(port) == [
        [NOTE_ON, 69, VELOCITY],
        [NOTE_ON, 69, VELOCITY],
        [NOTE_OFF, 69, 0],
        [NOTE_OFF, 69, 0],  # one note-off per note-on, not one for the pitch
    ]


def test_a_note_ending_where_the_next_begins_is_released_first() -> None:
    port = FakePort()
    player = MidiPortOut(port)

    player.set_program([(69, 0.1, 0.2, 0), (69, 0.3, 0.2, 0)], 1.0, ((0, 0, 100),))
    port.messages.clear()
    player.play()
    time.sleep(0.35)  # 0.1 + 0.2 lands a bit past 0.3, where the next note starts
    player.stop()

    assert note_messages(port)[:3] == [
        [NOTE_ON, 69, VELOCITY],
        [NOTE_OFF, 69, 0],  # the pitch is let go before it is struck again
        [NOTE_ON, 69, VELOCITY],
    ]


def test_the_external_synth_is_preferred_when_one_is_listening() -> None:
    player, name = open_player()
    assert isinstance(player, MidiPortOut)
    assert name == "TiMidity:Fake TiMidity port 0 128:0"  # the port the suite is given instead of a real one


@pytest.mark.parametrize("unavailable", ["no_ports", "backend_error", "open_error"])
def test_without_midi_output_playback_and_preview_stay_silent(monkeypatch, unavailable) -> None:
    fake_midi = types.ModuleType("rtmidi")

    class NoPorts:
        def get_ports(self) -> list[str]:
            if unavailable == "backend_error":
                raise RuntimeError("MIDI unavailable")
            return ["TiMidity"] if unavailable == "open_error" else []

        def open_port(self, index) -> None:
            raise RuntimeError("Port unavailable")

    fake_midi.MidiOut = NoPorts
    monkeypatch.setitem(sys.modules, "rtmidi", fake_midi)

    player, name = open_player()
    assert isinstance(player, SilentPlayer)
    assert player.silent and name == "No MIDI output"

    player.set_program([(60, 0.0, 1.0)], 1.0)
    assert player.duration == pytest.approx(1.0)
    player.play(0.5)
    player.preview(60)
    assert not player.is_playing and player.position == 0.5
    player.pause()
    assert player.position == 0.5
    player.stop()
    assert player.position == 0.0


@pytest.fixture
def scheduled_player(monkeypatch):
    class Clock:
        now = 100.0

        def monotonic(self):
            return self.now

        def sleep(self, seconds):
            self.now += seconds

    clock = Clock()
    timers = []

    class DeferredThread:
        def __init__(self, target, daemon):
            self.target = target

        def start(self):
            pass

        def join(self, timeout):
            pass

    class ManualTimer:
        def __init__(self, interval, function):
            self.interval = interval
            self.function = function
            self.cancelled = False

        def start(self):
            timers.append(self)

        def cancel(self):
            self.cancelled = True

        def fire(self):
            clock.now += self.interval
            self.function()

    class TimedPort(FakePort):
        def __init__(self):
            super().__init__()
            self.events = []

        def send_message(self, message):
            super().send_message(message)
            if message[0] & 0xF0 in (NOTE_ON, NOTE_OFF):
                self.events.append((clock.now, list(message)))

    monkeypatch.setattr(audio, "time", clock)
    monkeypatch.setattr(
        audio, "threading", types.SimpleNamespace(Thread=DeferredThread, Timer=ManualTimer, RLock=threading.RLock)
    )
    port = TimedPort()
    player = MidiPortOut(port)
    yield types.SimpleNamespace(clock=clock, timers=timers, port=port, player=player)
    player.stop()


@pytest.mark.parametrize("delay_ms", [-500, 0, 500])
@pytest.mark.parametrize("speed", [0.5, 1.0, 2.0])
def test_playback_delay_moves_both_events_in_wall_time(scheduled_player, delay_ms, speed) -> None:
    clock, player, port = scheduled_player.clock, scheduled_player.player, scheduled_player.port
    player.set_program([(60, 6.0, 0.4, 3)], speed)
    player.delay_ms = delay_ms
    finished = []
    player.finished.connect(lambda: finished.append(clock.now))

    player.play(5.0)
    player._schedule()

    assert [message for _at, message in port.events] == [[NOTE_ON | 3, 60, VELOCITY], [NOTE_OFF | 3, 60, 0]]
    assert [at for at, _message in port.events] == pytest.approx(
        [100.0 + 1.0 / speed + delay_ms / 1000.0, 100.0 + 1.4 / speed + delay_ms / 1000.0]
    )
    assert finished == pytest.approx([100.0 + 1.4 / speed + max(0, delay_ms) / 1000.0])
    assert player.position == pytest.approx(6.4)
    assert not player.is_playing


@pytest.mark.parametrize("operation", ["play", "seek", "resume"])
def test_negative_delay_starts_only_notes_covering_the_advanced_cursor(scheduled_player, operation) -> None:
    clock, player, port = scheduled_player.clock, scheduled_player.player, scheduled_player.port
    player.set_program(
        [
            (59, 9.9, 2.0),
            (60, 10.1, 0.1),
            (61, 10.3, 0.1),
            (62, 10.4, 0.1),
            (63, 10.45, 0.25),
            (64, 10.5, 0.25, 3),
            (65, 10.9, 0.1),
        ],
        1.0,
    )
    player.delay_ms = -500
    if operation == "play":
        player.play(10.0)
    else:
        player.play(9.0)
        if operation == "seek":
            player.seek(10.0)
        else:
            clock.now += 1.0
            player.pause()
            player.play(player.position)
    started = clock.now
    player._schedule()

    assert [message for _at, message in port.events] == [
        [NOTE_ON, 63, VELOCITY],
        [NOTE_ON | 3, 64, VELOCITY],
        [NOTE_OFF, 63, 0],
        [NOTE_OFF | 3, 64, 0],
        [NOTE_ON, 65, VELOCITY],
        [NOTE_OFF, 65, 0],
    ]
    assert [at - started for at, _message in port.events] == pytest.approx([0, 0, 0.2, 0.25, 0.4, 0.5])


@pytest.mark.parametrize("delay_ms", [-500, 0, 500])
def test_playback_does_not_retrigger_notes_starting_left_of_the_cursor(scheduled_player, delay_ms) -> None:
    player, port = scheduled_player.player, scheduled_player.port
    player.set_program([(60, 0.0, 3.0), (62, 1.0, 2.0)], 1.0)
    player.delay_ms = delay_ms
    player.play(1.0)
    player._schedule()
    assert [message[1] for _at, message in port.events] == [62, 62]


def test_negative_delay_in_a_gap_waits_for_the_next_onset(scheduled_player) -> None:
    player, port = scheduled_player.player, scheduled_player.port
    player.set_program([(60, 0.1, 0.1), (62, 0.8, 0.2)], 1.0)
    player.delay_ms = -500
    player.play()
    player._schedule()
    assert [message for _at, message in port.events] == [[NOTE_ON, 62, VELOCITY], [NOTE_OFF, 62, 0]]
    assert [at for at, _message in port.events] == pytest.approx([100.3, 100.5])


@pytest.mark.parametrize("delay_ms", [-500, 500])
def test_playback_delay_does_not_shift_the_transport_position(scheduled_player, delay_ms) -> None:
    player, clock = scheduled_player.player, scheduled_player.clock
    player.set_program([(60, 1.0, 1.0)], 0.5)
    player.delay_ms = delay_ms
    player.play(0.5)
    clock.now += 0.2
    assert player.position == pytest.approx(0.6)
    clock.now = 103.25
    assert player.position == pytest.approx(2.0)


def test_a_delay_change_waits_until_the_next_playback(scheduled_player) -> None:
    player, port = scheduled_player.player, scheduled_player.port
    player.set_program([(60, 1.0, 1.0)], 1.0)
    player.delay_ms = -100
    player.play()
    player.delay_ms = 200
    player._schedule()
    assert [at for at, _message in port.events] == pytest.approx([100.9, 101.9])

    port.events.clear()
    started = scheduled_player.clock.now
    player.play()
    player._schedule()
    assert [at - started for at, _message in port.events] == pytest.approx([1.2, 2.2])


@pytest.mark.parametrize("delay_ms", [-1000, 0])
def test_previews_with_nonpositive_delay_still_start_immediately(scheduled_player, delay_ms) -> None:
    player, port = scheduled_player.player, scheduled_player.port
    player.delay_ms = delay_ms
    player.preview(60, 0.2)
    assert port.events == [(100.0, [NOTE_ON, 60, VELOCITY])]
    assert len(scheduled_player.timers) == 1
    scheduled_player.timers[-1].fire()
    assert port.events[-1][0] == pytest.approx(100.2)
    assert port.events[-1][1] == [NOTE_OFF, 60, 0]


def test_positive_preview_delay_keeps_the_full_audition_duration(scheduled_player) -> None:
    player, port = scheduled_player.player, scheduled_player.port
    player.delay_ms = 150
    player.preview(60, 0.2)
    assert not port.events
    scheduled_player.timers[-1].fire()
    assert port.events == [(100.15, [NOTE_ON, 60, VELOCITY])]
    scheduled_player.timers[-1].fire()
    assert port.events[-1][0] == pytest.approx(100.35)
    assert port.events[-1][1] == [NOTE_OFF, 60, 0]


@pytest.mark.parametrize("operation", ["stop", "pause", "seek"])
def test_transport_changes_cancel_pending_delayed_previews(scheduled_player, operation) -> None:
    player, port = scheduled_player.player, scheduled_player.port
    player.delay_ms = 500
    player.preview(60)
    pending = scheduled_player.timers[-1]
    if operation == "seek":
        player.seek(1.0)
    else:
        getattr(player, operation)()
    assert pending.cancelled
    pending.fire()
    assert not port.events


def test_repeating_a_delayed_preview_cancels_its_pending_onset(scheduled_player) -> None:
    player, port = scheduled_player.player, scheduled_player.port
    player.delay_ms = 500
    player.preview(60)
    old = scheduled_player.timers[-1]
    player.preview(60)
    latest = scheduled_player.timers[-1]
    assert old.cancelled
    old.fire()
    assert not port.events
    latest.fire()
    assert [message for _at, message in port.events] == [[NOTE_ON, 60, VELOCITY]]


def test_repeating_a_sounding_preview_releases_it_before_the_next_delayed_onset(scheduled_player) -> None:
    player, port = scheduled_player.player, scheduled_player.port
    player.preview(60)
    old_release = scheduled_player.timers[-1]
    player.delay_ms = 500
    player.preview(60)
    assert old_release.cancelled
    assert [message for _at, message in port.events] == [[NOTE_ON, 60, VELOCITY], [NOTE_OFF, 60, 0]]
    old_release.fire()
    assert len(port.events) == 2
    scheduled_player.timers[-1].fire()
    assert port.events[-1][1] == [NOTE_ON, 60, VELOCITY]
