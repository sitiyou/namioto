# SPDX-License-Identifier: AGPL-3.0-only
"""Inspect a vocal clip for melody, candidate readings, or Japanese sung sounds."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from dataclasses import asdict, replace
from pathlib import Path
from typing import TYPE_CHECKING

from namioto.analysis import devices
from namioto.analysis.audio_window import AudioWindow

if TYPE_CHECKING:
    from namioto.analysis.align import Token

MODELS = ("game", "mms", "yohane", "romajiasr")


def analyze_clip(
    audio: str | Path,
    *,
    model: str,
    start: float = 0.0,
    end: float | None = None,
    audio_origin: float = 0.0,
    candidates: Sequence[str] = (),
    device: str = "cpu",
    model_dir: str | Path | None = None,
    game_size: str = "small",
    game_language: str | None = None,
) -> dict:
    """Analyze a file window independently of any editor or saved project.

    Select the window in file seconds. Note and token times use the recording
    clock, where `audio_origin` is the timestamp at file time zero.
    """
    if model not in MODELS:
        raise ValueError(f"unknown clip model {model!r}")
    window = AudioWindow(start, end, audio_origin)
    provider = devices.resolve(device)
    directory = Path(model_dir) if model_dir is not None else None
    result = {
        "audio": str(audio),
        "model": model,
        "window": {"start": start, "end": end, "audio_origin": audio_origin},
    }

    if model in ("game", "romajiasr") and candidates:
        raise ValueError("KRC candidates are only used by MMS and Yohane")
    if model == "romajiasr":
        from namioto.analysis import romajiasr

        return {**result, **romajiasr.recognize(audio, window, provider, directory)}
    if model == "game":
        from namioto.analysis import game

        backend = game.OnnxBackend(game.resolve_model(directory, size=game_size), provider=provider)
        notes = game.extract(backend, audio, window=window, language=game_language)
        return {**result, "notes": [{"onset": a, "offset": b, "pitch": pitch} for a, b, pitch in notes]}

    return {**result, "candidates": _compare_readings(audio, window, candidates, model, provider, directory)}


def _compare_readings(
    audio: str | Path,
    window: AudioWindow,
    candidates: Sequence[str],
    model: str,
    provider: str,
    directory: Path | None,
) -> list[dict]:
    from namioto.analysis import align
    from namioto.karaoke.sounds import natural_sounds, natural_tokens

    if isinstance(candidates, str) or not candidates:
        raise ValueError("supply one or more KRC candidates for the selected window")
    readings = [(text, tuple(natural_tokens(natural_sounds(text)))) for text in candidates]
    if any(not tokens for _, tokens in readings):
        raise ValueError("every KRC candidate must contain lyric sounds")

    waveform = window.load(audio, align.SAMPLE_RATE)
    seconds = waveform.size / align.SAMPLE_RATE
    directory = align.resolve_model(directory, model)
    align.fetch_fp16(directory, provider, model)
    backend = align.ChunkedBackend(align.OnnxBackend(align.model_file(directory, provider), provider=provider))
    emission = align.whole_emissions(backend, waveform)
    dictionary, blank = align.load_dictionary(directory / align.VOCAB_FILE)
    results = []
    for text, tokens in readings:
        measured = align.align_whole(align.Segment(0.0, seconds, tokens), emission, dictionary, blank_id=blank)
        results.append(
            {
                "text": text,
                "ctc_log_probability": measured.ctc_log_probability,
                "tokens": [asdict(_recording_token(token, window.onset, seconds)) for token in measured.tokens],
            }
        )
    return results


def _recording_token(token: Token, offset: float, duration: float) -> Token:
    def timestamp(seconds: float) -> float:
        return offset + min(seconds, duration)

    bounds = token.onset_interval
    return replace(
        token,
        start=timestamp(token.start) if token.start is not None else None,
        end=timestamp(token.end) if token.end is not None else None,
        onset_interval=(timestamp(bounds[0]), timestamp(bounds[1])) if bounds is not None else None,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="namioto-cli clip", description=__doc__)
    parser.add_argument("audio", type=Path, help="vocal recording or prepared clip")
    parser.add_argument("--model", choices=MODELS, required=True)
    parser.add_argument("--start", type=float, default=0.0, help="window start in file seconds")
    parser.add_argument("--end", type=float, help="window end in file seconds")
    parser.add_argument("--audio-origin", type=float, default=0.0, help="recording time at file time zero")
    parser.add_argument(
        "--candidate", dest="candidates", action="append", default=[], help="KRC for this clip; repeat to compare"
    )
    parser.add_argument("--device", choices=devices.RUN_CHOICES, default="cpu")
    parser.add_argument("--dir", dest="model_dir", type=Path, help="local model directory")
    parser.add_argument("--size", dest="game_size", default="small", help="GAME model size: small, medium or large")
    parser.add_argument("--language", dest="game_language", help="GAME language code, e.g. ja")
    args = parser.parse_args(argv)
    try:
        result = analyze_clip(**vars(args))
    except (OSError, ValueError, RuntimeError, KeyError) as error:
        parser.exit(2, f"cannot analyze clip: {error}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0
