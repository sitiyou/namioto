# Namioto

A FL Studio style MIDI note editor built with PyQt6: time on the horizontal axis, pitch on the
vertical axis, and notes you can draw, move, resize, and delete.

The name is a pun on *WaveTone*: 波音 (*namioto*, "sound of waves") is a literal Japanese
translation of *wave* + *tone*.

![namioto](docs/screenshot.png)

## Requirements

- [uv](https://docs.astral.sh/uv/) (Python 3.12 or newer)

## Run

```bash
uv run namioto                            # the editor
uv run namioto-tempo song.mp3             # estimate the tempo of a file
uv run namioto-tempo song.mp3 --local     # per-patch estimates, ~12 s resolution
uv run namioto-tempo song.mp3 --json      # machine readable, includes the tempo map
uv run namioto-spectrum song.mp3          # analyse into 84 note bands (C1-B7)
uv run namioto-spectrum song.mp3 --bench  # plus per-stage timings
uv run namioto-spectrum song.mp3 --threshold 1.2   # plus auto-filled note spans
uv run pytest                             # tests
uv run scripts/bench_spectrum.py          # spectrum benchmark
```

`uv` creates the virtual environment and installs the dependencies, including the bundled
TempoCNN model, on first run.

## Build

```bash
./build.sh wheel   # sdist + wheel             -> dist/*.whl, dist/*.tar.gz
./build.sh cli     # frozen tempo CLI          -> dist/namioto-tempo/
./build.sh cli spectrum                        # -> dist/namioto-spectrum/
./build.sh app     # frozen editor             -> dist/namioto/
./build.sh all
./build.sh clean
```

The ONNX model ships as package data: `uv build` puts it in the wheel, and the PyInstaller
targets collect it with `--collect-data namioto.models`. Bundled third-party models carry their
own license — see [namioto/models/README.md](namioto/models/README.md).

## Layout

```
namioto/tempo.py      TempoCNN tempo estimation (ONNX Runtime) + tempo map helpers, no Qt
namioto/spectrum.py   note-domain spectrum analysis (STFT → 84 note bands), no Qt
namioto/models/       bundled ONNX models
namioto/ui/           PyQt6 editor (app.py: window, controls.py: control bars, roll.py: widgets)
tests/                pytest
scripts/              developer tools (spectrum benchmark)
build.sh              packaging script
```

## License

Namioto is licensed under the **GNU Affero General Public License v3.0** — see [LICENSE](LICENSE).
The AGPL is required because the program reuses code from the GPL-3.0 licensed NoteDigger
project, reimplements the AGPL-3.0 licensed Essentia TempoCNN front end, and links PyQt6
(GPL-3.0 or commercial). See [NOTICE](NOTICE) for the full reasoning and the attributions.

The bundled tempo model is **not** AGPL: `namioto/models/deeptemp-k16-3.onnx` comes from
Essentia/TempoCNN and is licensed **CC BY-NC-SA 4.0** (attribution, non-commercial,
share-alike) — see [namioto/models/README.md](namioto/models/README.md). Builds that ship this
file are therefore non-commercial; drop the model or replace it to distribute commercially.

## Controls

The window has three control bars, each split into captioned blocks of related controls
(WaveTone's grouping):

| Bar | Blocks |
| --- | --- |
| Transport | **Playback** (rewind, stop, pause, play, forward, position readout), **Speed** (0.25x-2.00x with a `1.0` reset), **Tempo** (BPM), **Latency** (ms) |
| Edit | **Tools** (pen, select, snap grid, clear), **Division** (note icon = beats of the tempo map, clock icon = seconds) |
| Mix | **Spectrum** (brightness, contrast), **Volume** (audio, MIDI) |

Only the tool buttons and the snap grid drive the editor so far; the playback, spectrum and
volume controls are placeholders for the playback and spectrum milestones.

| Action | Input |
| --- | --- |
| Draw note | Pen tool: left drag on the empty grid (drag right to set the length) |
| Move note(s) | Left drag a note |
| Resize note | Left drag the right edge of a note |
| Select note | Left click |
| Add to selection | Shift or Ctrl + left click |
| Box select | Select tool: left drag on the grid, or Ctrl + left drag with either tool |
| Select all | Ctrl + A |
| Delete | Right click a note, or Delete / Backspace for the selection |
| Cancel a drag | Escape |
| Pan | Middle drag, or drag in the timeline ruler to scroll horizontally |
| Zoom time | Ctrl + wheel |
| Zoom pitch | Ctrl + Shift + wheel |
| Scroll | Wheel, Shift + wheel for horizontal |

The **Snap** combo sets the quantisation applied when drawing, moving, and resizing notes, and
**Clear** removes every note.
