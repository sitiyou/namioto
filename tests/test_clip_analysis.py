# SPDX-License-Identifier: AGPL-3.0-only
"""The clip API crops audio and reports model evidence on the recording clock."""

from __future__ import annotations

import json

import numpy as np
import pytest
import soundfile as sf

from namioto import cli
from namioto.analysis import align, game, model_store
from namioto.analysis.clip import analyze_clip


@pytest.fixture
def audio(tmp_path):
    path = tmp_path / "vocal.wav"
    samples = np.zeros(align.SAMPLE_RATE, dtype=np.float32)
    samples[4000:8000] = 0.5
    sf.write(path, samples, align.SAMPLE_RATE)
    return path


@pytest.mark.parametrize("model", ["mms", "yohane"])
def test_readings_share_one_model_pass_and_preserve_recording_time(audio, monkeypatch, model):
    dictionary = {letter: i for i, letter in enumerate("_sorauch")}
    waveform_lengths = []

    class Backend:
        def logits(self, waveform):
            waveform_lengths.append(len(waveform))
            np.testing.assert_allclose(waveform, 0.5)
            logits = np.full((12, len(dictionary)), -5.0)
            logits[np.arange(12), [dictionary[letter] for letter in "_s_o_r_a____"]] = 5.0
            return logits

    monkeypatch.setattr(align, "resolve_model", lambda directory, _model: audio.parent)
    monkeypatch.setattr(align, "fetch_fp16", lambda *_: None)
    monkeypatch.setattr(align, "OnnxBackend", lambda *_, **__: Backend())
    monkeypatch.setattr(align, "load_dictionary", lambda _: (dictionary, 0))

    result = analyze_clip(
        audio,
        model=model,
        start=0.25,
        end=0.5,
        audio_origin=42.0,
        candidates=("宇宙[う,ちゅう]", "宇宙[そら]", "(so).1(ra).1"),
    )

    standard, alternative, split_latin = result["candidates"]
    assert waveform_lengths == [4000]
    assert alternative["ctc_log_probability"] > standard["ctc_log_probability"]
    assert [token["text"] for token in alternative["tokens"]] == ["so", "ra"]
    assert alternative["tokens"][0]["start"] == pytest.approx(42.27)
    assert alternative["tokens"][1]["end"] <= 42.5
    assert [token["text"] for token in split_latin["tokens"]] == ["so", "ra"]


def test_game_uses_processed_clip_without_resetting_its_recording_time(audio, monkeypatch):
    class Backend:
        sr = align.SAMPLE_RATE
        timestep = 0.02
        languages = {"ja": 1}
        loop = False

        def encode(self, waveforms, durations):
            np.testing.assert_allclose(waveforms, np.full((1, 4000), 0.5))
            np.testing.assert_allclose(durations, [0.25])
            return None, None, np.zeros((1, 12), dtype=bool)

        def segment(self, *args):
            return None

        def durations_n_mask(self, *args):
            return [[0.15]], None

        def estimate(self, *args):
            return [[True]], [[66.25]]

    monkeypatch.setattr(game, "resolve_model", lambda directory, *, size: audio.parent)
    monkeypatch.setattr(game, "OnnxBackend", lambda *_, **__: Backend())

    result = analyze_clip(audio, model="game", start=0.25, end=0.5, audio_origin=42.0, game_language="ja")

    assert result["notes"] == [{"onset": 42.25, "offset": pytest.approx(42.4), "pitch": 66.25}]


def test_clip_checks_window_before_loading_a_model(audio):
    with pytest.raises(ValueError, match="greater than start"):
        analyze_clip(audio, model="game", start=0.5, end=0.25)


def test_romajiasr_cli_decodes_ctc_tokens_on_the_recording_clock(audio, monkeypatch, capsys):
    (audio.parent / "model.onnx").touch()
    (audio.parent / "phoneme_vocab.json").write_text('{"<blank>": 0, "a": 1, "shi": 2}')

    class Session:
        def run(self, outputs, feeds):
            assert outputs == ["pred_ids"]
            np.testing.assert_array_equal(feeds["input_values"], np.full((1, 4000), 0.5, dtype=np.float32))
            return [np.array([[0, 1, 1, 0, 1, 0, 2]])]

    monkeypatch.setattr(model_store, "session", lambda path, provider: Session())
    args = ["--model", "romajiasr", "--start", "0.25", "--end", "0.5", "--audio-origin", "42"]
    assert cli.main(["clip", str(audio), "--dir", str(audio.parent), *args]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["transcription"] == "a a shi"
    assert result["frame_seconds"] == 0.02
    assert result["tokens"] == [
        {"text": "a", "time": 42.2825, "start_frame": 1, "end_frame": 3},
        {"text": "a", "time": 42.3425, "start_frame": 4, "end_frame": 5},
        {"text": "shi", "time": 42.3825, "start_frame": 6, "end_frame": 7},
    ]


def test_romajiasr_bounds_the_inference_window(audio):
    sf.write(audio, np.zeros(31 * align.SAMPLE_RATE, dtype=np.float32), align.SAMPLE_RATE)
    with pytest.raises(ValueError, match="at most 30 seconds"):
        analyze_clip(audio, model="romajiasr")
