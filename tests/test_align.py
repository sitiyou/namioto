# SPDX-License-Identifier: AGPL-3.0-only

import json

import numpy as np
import pytest

from namioto import utils
from namioto.analysis import align, model_store


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    return tmp_path


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
DICTIONARY = {"[pad]": 0, "a": 1, "b": 2}


def test_the_alignment_parameters_are_remembered_and_checked():
    assert align.load_parameters() == align.default_parameters()

    align.save_parameters({"model": "yohane", "provider": "cuda", "quantize": 4})
    assert align.load_parameters() == {"model": "yohane", "provider": "cuda", "quantize": 4}

    align.save_parameters({"model": "nope", "provider": 7, "quantize": "eight"})
    assert align.load_parameters() == align.default_parameters()  # every value goes through its check

    align.parameter_path().write_text("{not json", encoding="utf-8")
    with pytest.warns(UserWarning):
        assert align.load_parameters() == align.default_parameters()


def test_the_alignment_cache_holds_the_raw_lines_and_reuses_them_by_their_inputs():
    rows = [[(0.0, 0.5), (0.5, 1.0), (None, None)]]
    problems = ["あい: empty"]
    align.save_alignment("/tmp/song.wav", "mms", "cpu", "あい\n", rows, problems)

    assert align.find_alignment("/tmp/song.wav", "mms", "cpu", "あい\n") == (rows, problems)
    assert align.find_alignment("/tmp/song.wav", "yohane", "cpu", "あい\n") is None
    assert align.find_alignment("/tmp/song.wav", "mms", "cpu", "うえ\n") is None


def test_a_broken_alignment_cache_is_no_alignment():
    target = align._store_path("/tmp/song.wav")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("{not json", encoding="utf-8")

    assert align.load_alignments("/tmp/song.wav") == {}
    assert align.find_alignment("/tmp/song.wav", "mms", "cpu", "あい\n") is None


def test_forced_align_visits_every_target_in_order():
    emission = align.log_softmax(np.asarray(LOGITS, dtype=np.float32))
    path, _scores = align.forced_align(emission, [1, 2])
    assert list(path) == [1, 1, 0, 2, 2, 0]


def test_forced_align_gives_up_when_the_window_cannot_hold_the_targets():
    emission = align.log_softmax(np.zeros((2, 3), dtype=np.float32))
    assert align.forced_align(emission, [1, 1, 1]) is None


def test_merge_tokens_collapses_runs_and_drops_the_blank():
    path = np.asarray([1, 1, 0, 2, 2, 0])
    scores = np.asarray([0.5, 1.0, 0.0, 1.0, 1.0, 0.0])
    spans = align.merge_tokens(path, scores)
    assert [(token, start, end) for token, start, end, _score in spans] == [(1, 0, 2), (2, 3, 5)]
    assert spans[0][3] == pytest.approx(0.75)


def test_align_gives_each_token_the_span_of_its_frames():
    aligned = align.align([align.Segment(0.0, 1.0, ("a", "b"))], FakeBackend(LOGITS), DICTIONARY, AUDIO)
    first, second = aligned[0].tokens
    assert first.text == "a" and second.text == "b"
    assert first.start == pytest.approx(0.0, abs=1e-6)
    assert first.end == pytest.approx(0.04, abs=1e-6)
    assert second.start == pytest.approx(0.06, abs=1e-6)
    assert second.end == pytest.approx(0.10, abs=1e-6)
    assert first.start < second.start


def test_align_groups_a_multi_character_token_into_one_span():
    aligned = align.align([align.Segment(0.0, 1.0, ("ab",))], FakeBackend(LOGITS), DICTIONARY, AUDIO)
    (only,) = aligned[0].tokens
    assert only.text == "ab"
    assert only.start == pytest.approx(0.0, abs=1e-6)
    assert only.end == pytest.approx(0.10, abs=1e-6)


def test_align_keeps_the_original_case_of_a_token():
    aligned = align.align([align.Segment(0.0, 1.0, ("A",))], FakeBackend(LOGITS), DICTIONARY, AUDIO)
    assert aligned[0].tokens[0].text == "A"
    assert aligned[0].tokens[0].start is not None


def test_align_times_a_character_outside_the_dictionary():
    aligned = align.align([align.Segment(0.0, 1.0, ("ab",))], FakeBackend(LOGITS), {"[pad]": 0, "a": 1}, AUDIO)
    assert aligned[0].tokens[0].start is not None


def test_align_returns_untimed_tokens_when_the_window_is_empty():
    aligned = align.align([align.Segment(1.0, 1.0, ("a", "b"))], FakeBackend(LOGITS), DICTIONARY, AUDIO)
    assert all(token.start is None for token in aligned[0].tokens)


def test_align_keeps_an_empty_token_untimed():
    aligned = align.align([align.Segment(0.0, 1.0, ("a", "", "b"))], FakeBackend(LOGITS), DICTIONARY, AUDIO)
    assert aligned[0].tokens[1].start is None
    assert aligned[0].tokens[0].start is not None and aligned[0].tokens[2].start is not None


def test_problems_names_the_failure_shapes():
    def segment(*starts):
        return align.AlignedSegment(0.0, 1.0, tuple(align.Token("x", start) for start in starts))

    assert align.problems(segment()) == ("empty",)
    assert align.problems(segment(0.0, 0.5)) == ()
    assert "nonmonotonic" in align.problems(segment(0.5, 0.4))
    assert "collapsed" in align.problems(segment(0.0, 0.02, 0.04, 0.06))
    assert align.problems(segment(0.0, 0.02)) == ()
    assert "diverged" in align.problems(segment(0.0, 0.5), reference=[0.5, 1.0])
    assert align.problems(segment(0.0, 0.5), reference=[0.01, 0.51]) == ()


def test_load_dictionary_lower_cases_and_finds_the_blank(tmp_path):
    path = tmp_path / "vocab.json"
    path.write_text(json.dumps({"[PAD]": 0, "A": 1, "あ": 2}), encoding="utf-8")
    dictionary, blank_id = align.load_dictionary(path)
    assert dictionary == {"[pad]": 0, "a": 1, "あ": 2}
    assert blank_id == 0


def test_load_dictionary_rejects_a_file_that_is_not_one(tmp_path):
    path = tmp_path / "vocab.json"
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError):
        align.load_dictionary(path)


def test_read_segments_accepts_a_list_or_an_object(tmp_path):
    path = tmp_path / "segments.json"
    path.write_text(json.dumps([{"start": 0, "end": 1, "tokens": ["a"]}]), encoding="utf-8")
    assert align.read_segments(path) == [align.Segment(0.0, 1.0, ("a",))]
    path.write_text(json.dumps({"segments": [{"start": 1, "end": 2, "tokens": ["b"]}]}), encoding="utf-8")
    assert align.read_segments(path) == [align.Segment(1.0, 2.0, ("b",))]


def test_model_dir_separates_the_two_models(tmp_path, monkeypatch):
    monkeypatch.setattr(utils.platformdirs, "user_data_dir", lambda name: str(tmp_path))
    assert align.model_dir("mms", "ja") == tmp_path / "models" / "mms" / "ja"
    assert align.model_dir("yohane", "ja") == tmp_path / "models" / "yohane" / "ja"
    with pytest.raises(ValueError):
        align.model_dir("other", "ja")


def test_resolve_model_falls_back_to_the_environment(tmp_path, monkeypatch):
    (tmp_path / align.MODEL_FILE).write_bytes(b"")
    (tmp_path / align.VOCAB_FILE).write_text("{}", encoding="utf-8")
    monkeypatch.setenv(align.MODEL_ENV, str(tmp_path))
    assert align.resolve_model() == tmp_path


def test_resolve_model_reads_a_file_as_its_directory(tmp_path):
    model = tmp_path / align.MODEL_FILE
    model.write_bytes(b"")
    (tmp_path / align.VOCAB_FILE).write_text("{}", encoding="utf-8")
    assert align.resolve_model(model) == tmp_path


def test_resolve_model_rejects_a_directory_without_the_model(tmp_path):
    with pytest.raises(FileNotFoundError):
        align.resolve_model(tmp_path)


def test_resolve_model_downloads_what_is_not_installed(tmp_path, monkeypatch):
    monkeypatch.delenv(align.MODEL_ENV, raising=False)
    monkeypatch.setattr(utils.platformdirs, "user_data_dir", lambda name: str(tmp_path))
    asked: list[tuple] = []

    def install(name, parts=(), progress=None, **rest):
        asked.append((name, parts))
        return tmp_path

    monkeypatch.setattr(model_store, "install", install)

    assert align.resolve_model() == tmp_path
    assert asked == [("aligner", ("mms", "ja"))]
