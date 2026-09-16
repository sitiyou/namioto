# SPDX-License-Identifier: AGPL-3.0-only

import json

import numpy as np
import pytest

from namioto import align


class FakeBackend:
    """A CTC model that answers every window with the same hand-built posteriors."""

    def __init__(self, logits):
        self._logits = np.asarray(logits, dtype=np.float32)

    def logits(self, waveform):
        return self._logits


AUDIO = np.zeros(align.SAMPLE_RATE * 2, dtype=np.float32)

# blank, "a", "b": a over frames 0-1, a rest, b over frames 3-4, a rest
LOGITS = [
    [0.0, 20.0, 0.0],
    [0.0, 20.0, 0.0],
    [20.0, 0.0, 0.0],
    [0.0, 0.0, 20.0],
    [0.0, 0.0, 20.0],
    [20.0, 0.0, 0.0],
]
DICTIONARY = {"<pad>": 0, "a": 1, "b": 2}


def test_merge_repeats_collapses_each_run():
    path = [(0, 0, 1.0), (0, 1, 0.5), (1, 3, 1.0), (1, 4, 1.0)]
    assert align.merge_repeats(path, "ab") == [("a", 0, 2, 0.75), ("b", 3, 5, 1.0)]


def test_backtrack_walks_the_text_and_stays_monotonic():
    emission = align.log_softmax(np.asarray(LOGITS, dtype=np.float32))
    tokens = [DICTIONARY["a"], DICTIONARY["b"]]
    path = align.backtrack(align.get_trellis(emission, tokens), emission, tokens)
    assert path is not None
    assert [token for token, _, _ in path] == sorted(token for token, _, _ in path)
    assert {token for token, _, _ in path} == set(range(len(tokens)))


def test_backtrack_gives_up_when_no_frame_can_hold_the_text():
    trellis = np.full((3, 3), -np.inf, dtype=np.float32)
    assert align.backtrack(trellis, np.zeros((2, 3), dtype=np.float32), [1]) is None


def test_align_times_characters_inside_their_window():
    aligned = align.align([align.Segment(0.0, 1.0, "ab")], FakeBackend(LOGITS), DICTIONARY, AUDIO)
    first, second = aligned[0].chars
    assert first.char == "a" and second.char == "b"
    assert first.start is not None and second.start is not None and second.end is not None
    # six frames over the one-second window, the second character one frame after the first
    assert first.start == pytest.approx(0.0, abs=1e-3)
    assert second.start == pytest.approx(1 / 6, abs=1e-3)
    assert first.start < second.start
    assert second.end <= 1.0


def test_align_keeps_the_original_case_of_a_character():
    aligned = align.align([align.Segment(0.0, 1.0, "Ab")], FakeBackend(LOGITS), DICTIONARY, AUDIO)
    assert [char.char for char in aligned[0].chars] == ["A", "b"]


def test_align_keeps_every_input_character():
    aligned = align.align([align.Segment(0.0, 1.0, "a b")], FakeBackend(LOGITS), DICTIONARY, AUDIO)
    assert [char.char for char in aligned[0].chars] == ["a", " ", "b"]
    assert aligned[0].chars[1].start is None


def test_align_times_a_character_outside_the_dictionary():
    aligned = align.align([align.Segment(0.0, 1.0, "ab")], FakeBackend(LOGITS), {"<pad>": 0, "a": 1}, AUDIO)
    assert aligned[0].chars[1].start is not None


def test_align_returns_untimed_characters_when_the_window_is_empty():
    aligned = align.align([align.Segment(1.0, 1.0, "ab")], FakeBackend(LOGITS), DICTIONARY, AUDIO)
    assert all(char.start is None for char in aligned[0].chars)


def test_problems_names_the_failure_shapes():
    def segment(*starts):
        return align.AlignedSegment("x", 0.0, 1.0, tuple(align.Char("x", start) for start in starts))

    assert align.problems(segment()) == ("empty",)
    assert align.problems(segment(0.0, 0.5)) == ()
    assert "nonmonotonic" in align.problems(segment(0.5, 0.4))
    assert "collapsed" in align.problems(segment(0.0, 0.02, 0.04, 0.06))
    assert align.problems(segment(0.0, 0.02)) == ()
    assert "diverged" in align.problems(segment(0.0, 0.5), reference=[0.5, 1.0])
    assert align.problems(segment(0.0, 0.5), reference=[0.01, 0.51]) == ()


def test_load_dictionary_lower_cases_and_finds_the_blank(tmp_path):
    path = tmp_path / "vocab.json"
    path.write_text(json.dumps({"<pad>": 0, "A": 1, "あ": 2}), encoding="utf-8")
    dictionary, blank_id = align.load_dictionary(path)
    assert dictionary == {"<pad>": 0, "a": 1, "あ": 2}
    assert blank_id == 0


def test_load_dictionary_rejects_a_file_that_is_not_one(tmp_path):
    path = tmp_path / "vocab.json"
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError):
        align.load_dictionary(path)


def test_read_segments_accepts_a_list_or_an_object(tmp_path):
    path = tmp_path / "segments.json"
    path.write_text(json.dumps([{"start": 0, "end": 1, "text": "a"}]), encoding="utf-8")
    assert align.read_segments(path) == [align.Segment(0.0, 1.0, "a")]
    path.write_text(json.dumps({"segments": [{"start": 1, "end": 2, "text": "b"}]}), encoding="utf-8")
    assert align.read_segments(path) == [align.Segment(1.0, 2.0, "b")]


def test_resolve_model_falls_back_to_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv(align.MODEL_ENV, str(tmp_path))
    assert align.resolve_model() == tmp_path


def test_resolve_model_reads_a_file_as_its_directory(tmp_path):
    model = tmp_path / align.MODEL_FILE
    model.write_bytes(b"")
    assert align.resolve_model(model) == tmp_path


def test_resolve_model_says_what_is_missing(tmp_path, monkeypatch):
    monkeypatch.delenv(align.MODEL_ENV, raising=False)
    monkeypatch.setattr(align.platformdirs, "user_data_dir", lambda name: str(tmp_path))
    with pytest.raises(FileNotFoundError):
        align.resolve_model()
