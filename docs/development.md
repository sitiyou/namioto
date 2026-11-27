# Development

[README](../README.md) · [CLI guide](cli.md)

## Source setup

Install uv, Python 3.14 and a C++17 compiler. Development and CI both read
Python 3.14 from `.python-version`. From the repository root, run

```bash
uv sync --locked --extra cpu
uv run namioto
```

Choose `--extra cuda` or `--extra webgpu` instead if needed; see [devices](cli.md#devices-and-models).
Add `--extra midi` to enable note playback through an external MIDI synthesiser.
The native audio extension is built with meson-python and pybind11. Build isolation is disabled
for this package, so the development group provides its build tools. Editing native sources
rebuilds the extension on the next import.

## Tests and lint

```bash
uv run pytest
uv run pytest -m slow
uv run pytest -m ''
uvx ruff check namioto tests
uvx ruff format namioto tests
uv run scripts/bench_spectrum.py
```

The lyric pixel-rendering test requires Noto CJK fonts (`fonts-noto-cjk` on Ubuntu) and uses
Noto Sans CJK JP explicitly so glyph rendering does not depend on the desktop's default font.

The default test run excludes slow beat-tracker tests. Tests must not require a display,
a synthesiser or an audio device. See `tests/conftest.py` and `tests/test_ui.py`
for the enforced boundaries.

## Packaging

```bash
./build.sh wheel   # dist/*.whl and dist/*.tar.gz
./build.sh cli     # dist/namioto-cli/
./build.sh ctl     # dist/namioto-ctl/
./build.sh app     # dist/namioto/
./build.sh all
./build.sh clean
```

### GitHub Actions and releases

The CI workflow runs lint and the default headless tests on Ubuntu 24.04 for pull requests,
main-branch commits, version tags and manual runs. Slow tests are excluded.

Every run builds the CPU-only GUI on Ubuntu 24.04 x86_64 and Windows x64 after checks pass,
and uploads the AppImage for Linux and the frozen application directory for Windows as Actions
artifacts. Push a version tag matching `pyproject.toml` to build the Python wheels and publish a
GitHub Release after the desktop and wheel builds pass. The release includes generated notes and
these assets.

- `namioto-linux-x86_64.AppImage`
- `namioto-windows-x86_64.zip` (extract the entire archive and run `namioto/namioto.exe`)
- Python 3.14 wheels for Linux x86-64 (`manylinux_2_34`) and Windows x64
- `SHA256SUMS`

On a retry, the workflow refreshes an existing draft's assets before publishing it. It reuses
the release if that tag is already published. CI verifies packaging. Test application
startup, desktop interaction and audio devices before pushing a release tag.
Windows builds are unsigned and may trigger SmartScreen. Linux builds target Ubuntu 24.04 and
newer compatible systems; older glibc distributions such as Ubuntu 22.04 are not supported.
AppImage execution normally requires FUSE; where unavailable, use its
`--appimage-extract-and-run` option.

The GitHub Release hosts the Python wheels. Install the matching wheel into your Python
environment with the desired analysis extra. The desktop packages include external MIDI
output and keep settings and downloaded models in the normal user directories.

To package locally, first run `bash build.sh app`, then:

```bash
# Linux: download appimagetool 1.9.1 for x86_64 and make it executable first
python scripts/package_release.py appimage --appimagetool /path/to/appimagetool-x86_64.AppImage
# Windows: zip the frozen application, which needs no compiler environment
python scripts/package_release.py windows
```

Packages are written to `dist/release/`.

Published wheels include the native extension; source distributions require compilation.
Neither wheels nor frozen builds bundle analysis models. Model publishing uses
`scripts/upload_models.sh`; preserve each model's license and attribution in [NOTICE](../NOTICE).

## Model conversion

The aligner runs converted ONNX models. The separate conversion group uses the official
PyTorch CPU wheel index for torch and torchaudio.

```bash
uv run --group export scripts/export_align_model.py --model mms
uv run --group export scripts/export_align_model.py --model yohane
```

MMS conversion uses torch and torchaudio; yohane uses torch and transformers. Both need ONNX.
The checkpoints are Meta's MMS forced aligner and
`NextFire/mms-300m-ForcedAligner-karaoke-ja-Latn`, respectively.

## Karaoke production skill

The [Namioto karaoke skill](../skills/namioto-nicokara/SKILL.md) lives under `skills/` as part of the source tree. Update it alongside changes to the IPC commands, lyric mapping or export workflow that its instructions use.

## Code map

| Location | Responsibility |
| --- | --- |
| `namioto/ui/` | Qt editor, dialogs, spectrum rendering and remote-control server |
| `namioto/analysis/` | Spectrum, tempo, transcription, alignment, model downloads and devices |
| `namioto/karaoke/` | KRC parsing, transformations and subtitle output |
| `namioto/lyricmap/` | Lyrics-to-notes mapping and export validation |
| `namioto/document.py`, `project.py`, `midi.py` | Notes, project persistence and MIDI interchange |
| `namioto/settings.py`, `state.py` | Preferences and remembered UI/new-project values |
| `namioto/cli.py`, `ipc.py` | Headless tasks and remote-control client/protocol |
| `audio/`, `vendor/`, `meson.build` | Native audio backend and vendored dependencies |
| `tests/`, `scripts/` | Tests and developer tools |

Read module docstrings before changes. Keep Qt imports under `namioto/ui/`; core analysis,
project, MIDI and lyrics modules must remain usable without Qt. Note editing uses beats
internally; project files and MIDI use seconds. Keep conversions at the piano-roll boundary,
and keep each user gesture as one undo step.
