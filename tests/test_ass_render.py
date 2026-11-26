# SPDX-License-Identifier: AGPL-3.0-only
"""Optional headless libass integration with a one line-timed event."""

import shutil
import subprocess

import numpy as np
import pytest

from namioto.karaoke import generate_ass


def test_plain_ass_displays_only_during_its_interval(tmp_path):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg is required for the render check")
    filters = subprocess.run(["ffmpeg", "-filters"], capture_output=True, text=True, check=True).stdout
    if " ass " not in filters:
        pytest.skip("ffmpeg was built without libass")
    (tmp_path / "lyrics.ass").write_text(generate_ass("Lyrics", [[(1, 2)]]), encoding="utf-8")
    counts = []
    for timestamp in (0.5, 1.5, 2.5):
        result = subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=c=black:s=1920x1080:r=10:d=4",
                "-vf",
                "ass=lyrics.ass",
                "-ss",
                str(timestamp),
                "-frames:v",
                "1",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "rgb24",
                "pipe:1",
            ],
            cwd=tmp_path,
            capture_output=True,
            check=True,
        )
        pixels = np.frombuffer(result.stdout, dtype=np.uint8).reshape(1080, 1920, 3)
        counts.append((pixels > 180).all(axis=2).sum())
    assert counts[0] == counts[2] == 0
    assert counts[1] > 500
