# SPDX-License-Identifier: AGPL-3.0-only
"""Read Japanese sung sounds with the official RomajiASR v1.0.0 ONNX graph."""

from __future__ import annotations

import json
from dataclasses import replace
from itertools import groupby
from pathlib import Path

from namioto.analysis import devices, model_store
from namioto.analysis.audio_window import AudioWindow

SAMPLE_RATE = 16_000
FRAME_SECONDS = 320 / SAMPLE_RATE
FIRST_FRAME_CENTER = 200 / SAMPLE_RATE
# HuBERT attention grows quadratically with the window. Keep single-pass jobs on short passages.
MAX_SECONDS = 30


def recognize(audio: str | Path, window: AudioWindow, provider: str, directory: Path | None = None) -> dict:
    """Return the CTC token sequence and approximate token positions on the recording clock."""
    limit = window.start + MAX_SECONDS + FRAME_SECONDS
    selected = window if window.end is not None and window.end <= limit else replace(window, end=limit)
    waveform = selected.load(audio, SAMPLE_RATE)
    if waveform.size > MAX_SECONDS * SAMPLE_RATE:
        raise ValueError(f"select at most {MAX_SECONDS} seconds for RomajiASR")
    if waveform.size < 400:
        raise ValueError("RomajiASR needs at least 25 ms of audio")

    if error := devices.validate():
        raise RuntimeError(error)
    directory = model_store.resolve("romajiasr", directory)
    vocab = json.loads((directory / "phoneme_vocab.json").read_text(encoding="utf-8"))
    labels = {number: text for text, number in vocab.items()}
    try:
        session = model_store.session(directory / "model.onnx", provider)
        (pred_ids,) = session.run(["pred_ids"], {"input_values": waveform[None, :]})
    except Exception as error:
        raise RuntimeError(f"RomajiASR inference failed: {error}") from error

    tokens = []
    frame = 0
    for number, repeated in groupby(pred_ids[0].tolist()):
        end = frame + sum(1 for _ in repeated)
        if number != vocab["<blank>"]:
            tokens.append(
                {
                    "text": labels.get(number, "<unk>"),
                    "time": round(window.onset + FIRST_FRAME_CENTER + frame * FRAME_SECONDS, 6),
                    "start_frame": frame,
                    "end_frame": end,
                }
            )
        frame = end

    return {
        "transcription": " ".join(token["text"] for token in tokens),
        "frame_seconds": FRAME_SECONDS,
        "tokens": tokens,
    }
