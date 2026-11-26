# SPDX-License-Identifier: AGPL-3.0-only
"""Read lyrics and exact note timings from a saved project or matched KRC and MIDI."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from namioto import midi, project
from namioto.karaoke.sounds import SoundLine, natural_sounds
from namioto.lyric_colors import DEFAULT_COLORS
from namioto.lyricmap import constraints, faithful
from namioto.lyricmap.channels import split
from namioto.lyricmap.faithful import Span
from namioto.lyricmap.notes import TimedNote, resolve
from namioto.lyricmap.verify import faithful_gate


@dataclass(frozen=True)
class TimedLyrics:
    """Lyric lines and their per-sound spans, in absolute song seconds.

    Each entry in spans matches the sound at the same position in its line.
    Dropped sounds have no span. The line keeps the text, readings, chapter and channels.
    """

    lines: tuple[SoundLine, ...]
    spans: tuple[tuple[Span, ...], ...]
    bpm: float
    channel_colors: dict[int, str] = field(default_factory=dict)


def build_song(krc: str, data: bytes, *, wavetone: bool = False) -> TimedLyrics:
    imported = midi.read_bytes(data, wavetone=wavetone)
    if imported.dropped or imported.left_out:
        raise ValueError("the MIDI contains incomplete notes or unsupported channels")
    return _build_note_song(krc, imported.notes, imported.bpm)


def load_project_input(path: str | Path) -> TimedLyrics:
    """Read saved lyrics and note times without opening the project's audio."""
    source = project.load(path)
    if source.lyrics is None:
        raise ValueError("the project has no lyrics")
    colors = {
        channel: getattr(source.settings.lyric_colors, f"channel_{channel}").lstrip("#").upper()
        for channel in range(len(DEFAULT_COLORS))
    }
    return _build_note_song(source.lyrics.text, source.notes, source.settings.tempo.bpm, colors=colors)


def _build_note_song(
    krc: str, notes: Sequence[project.Note], bpm: float, *, colors: dict[int, str] | None = None
) -> TimedLyrics:
    lines = natural_sounds(krc)
    if not lines or not any(line.sounds for line in lines):
        raise ValueError("the lyrics have no sounds")
    spans = [[None] * len(line.sounds) for line in lines]
    for channel, part in split(krc).items():
        text = part.text
        resolved_notes = resolve(
            (
                TimedNote(note.start, note.start + note.duration, note.pitch, index)
                for index, note in enumerate(notes)
                if note.channel == channel
            ),
            include_branches=False,
        )
        rows = faithful.read(text, resolved_notes.stream)
        dropped = tuple(ref for ref, (_run, slots) in constraints.read(text).items() if slots == 0)
        gate = faithful_gate(
            rows,
            resolved_notes.stream,
            filtered=resolved_notes.filtered,
            subtitle=True,
            dropped=dropped,
        )
        if not gate.open():
            raise ValueError(f"channel {channel}: " + "; ".join(problem.message for problem in gate.problems))
        for refs, row in zip(part.refs, rows, strict=True):
            for ref, span in zip(refs, row, strict=True):
                spans[ref.line][ref.index] = span
    return TimedLyrics(tuple(lines), tuple(tuple(row) for row in spans), bpm, colors or {})
