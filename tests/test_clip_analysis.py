# SPDX-License-Identifier: AGPL-3.0-only
"""The clip API crops audio and reports model evidence on the recording clock."""

from __future__ import annotations

import numpy as np
import pytest
import soundfile as sf

from namioto.analysis import align, game
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
        candidates=("宇宙[う,ちゅう]", "宇宙[そら]"),
    )

    standard, alternative = result["candidates"]
    assert waveform_lengths == [4000]
    assert alternative["ctc_log_probability"] > standard["ctc_log_probability"]
    assert [token["text"] for token in alternative["tokens"]] == ["so", "ra"]
    assert alternative["tokens"][0]["start"] == pytest.approx(42.27)
    assert alternative["tokens"][1]["end"] <= 42.5


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
