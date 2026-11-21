# /// script
# requires-python = ">=3.11"
# dependencies = ["matplotlib", "numpy", "soundfile"]
# ///
"""Inspect a selected mono/stereo audio interval. Times use the input file's clock."""

import argparse
import math
from pathlib import Path

import matplotlib
import numpy as np
import soundfile as sf

matplotlib.use("Agg")
from matplotlib import pyplot as plt


def spectrum(samples, rate):
    """One-sided peak amplitudes with a 46 ms Hann window and a 5 ms hop."""
    size = min(len(samples), max(3, round(0.046 * rate)))
    hop = max(1, round(0.005 * rate))
    window = np.hanning(size)
    frames = np.lib.stride_tricks.sliding_window_view(samples, size)[::hop]
    amplitudes = np.abs(np.fft.rfft(frames * window)) * (2 / window.sum())
    return (np.arange(len(frames)) * hop + (size - 1) / 2) / rate, np.fft.rfftfreq(size, 1 / rate), amplitudes.T


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    parser.add_argument("start", type=float, help="start in seconds")
    parser.add_argument("end", type=float, help="end in seconds")
    parser.add_argument("--png", type=Path, help="write spectrogram with a shared dB scale")
    parser.add_argument("--views", nargs="+", choices=["original", "mid", "side"], default=None)
    parser.add_argument("--mark", action="append", default=[], metavar="SECONDS=LABEL")
    parser.add_argument("--window-ms", type=float, default=100, help="RMS table window (default 100 ms)")
    args = parser.parse_args()
    with sf.SoundFile(args.audio) as source:
        rate = source.samplerate
        if source.channels not in (1, 2):
            parser.error("select a mono or stereo input")
        if not 0 <= args.start < args.end <= len(source) / rate:
            parser.error("select an interval inside the audio file")
        first, end = round(args.start * rate), round(args.end * rate)
        if end - first < 3:
            parser.error("select at least three samples")
        source.seek(first)
        data = source.read(end - first, always_2d=True)
    if not math.isfinite(args.window_ms) or args.window_ms <= 0:
        parser.error("--window-ms must be positive and finite")
    stereo = data.shape[1] == 2
    channels = {"mono": data[:, 0]}
    if stereo:
        left, right = data.T
        channels = {"left": left, "right": right, "mid": (left + right) / 2, "side": (left - right) / 2}
    views = args.views or (["original", "mid", "side"] if stereo else ["original"])
    if not stereo and any(view != "original" for view in views):
        parser.error("mid and side require stereo input")
    marks = []
    for mark in args.mark:
        when, sep, label = mark.partition("=")
        try:
            position = float(when)
        except ValueError:
            parser.error("use --mark SECONDS=LABEL")
        if not sep or not label or not args.start <= position <= args.end:
            parser.error("each marker needs a label and a time inside the selection")
        marks.append((position, label))

    step = max(1, round(args.window_ms * rate / 1000))
    print("start_s,end_s," + ",".join(name + "_rms_dbfs" for name in channels))
    for i in range(0, len(data), step):
        j = min(len(data), i + step)
        values = []
        for samples in channels.values():
            rms = np.sqrt(np.mean(samples[i:j] ** 2))
            values.append(f"{20 * math.log10(rms):.1f}" if rms else "-inf")
        print(f"{(first+i)/rate:.6f},{(first+j)/rate:.6f}," + ",".join(values))
    if args.png is None:
        return
    fig, axes = plt.subplots(len(views), sharex=True, squeeze=False, figsize=(10, 2.3 * len(views) + 1),
                             layout="constrained")
    for axis, view in zip(axes.flat, views, strict=True):
        signals = data.T if view == "original" else [channels[view]]
        spectra = []
        for signal in signals:
            times, frequencies, amplitude = spectrum(signal, rate)
            spectra.append(amplitude ** 2)
        db = 10 * np.log10(np.maximum(np.mean(spectra, axis=0), 1e-12))
        mesh = axis.pcolormesh(times + first / rate, frequencies, db, shading="nearest", vmin=-80, vmax=0)
        axis.set(title=view.capitalize(), ylabel="Hz", ylim=(0, min(5000, rate / 2)), xlim=(args.start, args.end))
        for position, label in marks:
            axis.axvline(position, color="white", linestyle="--", linewidth=1)
            if axis is axes.flat[0]:
                axis.text(position, 0.96, label, transform=axis.get_xaxis_transform(), color="white", va="top",
                          ha="right" if position > (args.start + args.end) / 2 else "left",
                          bbox={"facecolor": "black", "alpha": 0.7, "pad": 2})
    axes.flat[-1].set_xlabel("Source audio time (s)")
    fig.colorbar(mesh, ax=list(axes.flat), label="Spectral amplitude (dBFS)")
    fig.savefig(args.png, dpi=140)
    plt.close(fig)


if __name__ == "__main__":
    main()
