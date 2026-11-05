# SPDX-License-Identifier: AGPL-3.0-only
"""Tempo (BPM) estimation using the dfttempo_awd logic in WaveTone 2.73's awlib.dll."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import librosa
import numpy as np

FRAME = 1024
COARSE_BPM = np.arange(60, 240)
FINE_SPAN = 0.5
FINE_STEP = 0.01
PHASE_BITS = 12
PHASE_SIZE = 1 << PHASE_BITS
PHASE_MASK = PHASE_SIZE - 1
SCORE_CHUNK = 16
_PHASE = np.arange(PHASE_SIZE, dtype=np.float64) * (2.0 * np.pi / PHASE_SIZE)
_SIN = np.sin(_PHASE)
_COS = np.cos(_PHASE)


def volume_change(mono: np.ndarray, sample_rate: int, frame: int = FRAME) -> tuple[np.ndarray, float]:
    """The absolute change of the frame's peak volume, and the rate it is sampled at."""
    count = len(mono) // frame
    if count < 2:
        raise ValueError("Audio is too short to measure a tempo from")
    volume = np.abs(mono[: count * frame].reshape(count, frame)).max(axis=1)
    return np.abs(np.diff(volume)), sample_rate / frame


def score(envelope: np.ndarray, rate: float, bpms: np.ndarray) -> np.ndarray:
    """Match WaveTone's 4096-step phase DFT at BPM and 2x BPM, with equal weights."""
    values = np.asarray(envelope, dtype=np.float64)
    samples = np.arange(len(values), dtype=np.float64)
    out = np.empty(len(bpms))
    for start in range(0, len(bpms), SCORE_CHUNK):
        candidates = np.asarray(bpms[start : start + SCORE_CHUNK], dtype=np.float64)
        phase = samples[None, :] * (PHASE_SIZE * candidates[:, None] / (30.0 * rate))
        phase_index = phase.astype(np.int64) & PHASE_MASK
        half_index = (phase.astype(np.int64) >> 1) & PHASE_MASK

        # awlib accumulates the complex DFT components from a 4096-entry trig table.
        fundamental_real = _COS[half_index] @ values
        fundamental_imag = _SIN[half_index] @ values
        harmonic_real = _COS[phase_index] @ values
        harmonic_imag = _SIN[phase_index] @ values
        out[start : start + len(candidates)] = (
            np.hypot(fundamental_real, fundamental_imag)
            + np.hypot(harmonic_real, harmonic_imag)
        )
    return out


def estimate_array(mono: np.ndarray, sample_rate: int, *, frame: int = FRAME) -> float:
    envelope, rate = volume_change(mono, sample_rate, frame)
    if not envelope.any():
        raise ValueError("Found no volume change to measure a tempo from")
    coarse = COARSE_BPM[int(np.argmax(score(envelope, rate, COARSE_BPM)))]
    fine = np.round(np.arange(coarse - FINE_SPAN, coarse + FINE_SPAN + 1e-9, FINE_STEP), 2)
    return float(fine[int(np.argmax(score(envelope, rate, fine)))])


def estimate(path: str | Path, *, frame: int = FRAME) -> float:
    mono, sample_rate = librosa.load(path, sr=None, mono=True)
    return estimate_array(np.clip(mono, -1.0, 1.0), sample_rate, frame=frame)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="namioto-cli wavetone", description=__doc__)
    parser.add_argument("audio", help="input audio file")
    parser.add_argument("--frame", type=int, default=FRAME, help="volume envelope frame in samples")
    parser.add_argument("--json", action="store_true", help="print the estimate as JSON")
    args = parser.parse_args(argv)

    bpm = estimate(args.audio, frame=args.frame)

    if args.json:
        json.dump({"bpm": bpm}, sys.stdout, indent=2)
        print()
        return 0

    print(f"Song BPM: {bpm:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
