# Namioto

An open-source editor that follows [WaveTone](https://ackiesound.ifdef.jp/)'s feature set: analyse an
audio file into a note-domain spectrum, draw that behind a piano roll, then transcribe what you see
into notes - time on the horizontal axis, pitch on the vertical axis, with notes you can draw, move,
resize and delete. Notes play back through a MIDI synth or the built-in one, and the file plays along
with them.

![namioto](docs/screenshot.png)

## Requirements

- [uv](https://docs.astral.sh/uv/) (Python 3.12 or newer)

## Run

```bash
uv run namioto                            # the editor
uv run namioto song.mp3                   # the editor with the audio analysed into a spectrum
uv run namioto song.mp3 --channels both --gain 300   # analysis options
uv run namioto-tempo song.mp3             # estimate the tempo of a file (beat tracking + fit)
uv run namioto-tempo song.mp3 --local     # per-window estimates, 12 s wide, 6 s apart
uv run namioto-tempo song.mp3 --json      # machine readable, includes every beat
uv run namioto-tempocnn song.mp3          # the same with the TempoCNN model (runner-up)
uv run namioto-spectrum song.mp3          # analyse into 84 note bands (C1-B7)
uv run namioto-spectrum song.mp3 --bench  # plus per-stage timings
uv run namioto-spectrum song.mp3 --threshold 1.2   # plus auto-filled note spans
uv run namioto-game song.mp3 --model DIR  # extract the notes of a singing voice (GAME's models)
uv run pytest                             # tests
uv run scripts/bench_spectrum.py          # spectrum benchmark
```

`uv` creates the virtual environment and installs the dependencies, including the bundled
TempoCNN model used by `namioto-tempocnn` and `namioto.tempo`, on first run.

## Spectrum

Passing an audio file draws its note-domain spectrum behind the piano roll, the way noteDigger and
WaveTone show it: one column per 25 ms frame (40 frames per second), one row per note band (C1 to B7),
coloured from dark
blue through green to red as the energy rises, with the octave lines of the pitch axis. The
**Spectrum** block sets the two display parameters — gain (how much energy reaches full red) and
contrast (the exponent applied to the energy). The analysis runs in a background thread and reports
progress in the status bar; `--channels` picks the channels to analyse (mono, left, right, sum,
side, both), `--t-num` the frames per second.

![spectrum](docs/spectrum.png)

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
namioto/beats.py      tempo estimation by beat tracking (librosa) + least-squares fit, no Qt
namioto/tempo.py      TempoCNN tempo estimation (ONNX Runtime) + tempo map helpers, no Qt
namioto/spectrum.py   note-domain spectrum analysis (STFT → 84 note bands), no Qt
namioto/playback.py   note synthesis: pitches rendered into one audio buffer, no Qt
namioto/models/       bundled ONNX models
namioto/ui/           PyQt6 editor (app.py: window, controls.py: control bars, roll.py: widgets,
                      spectrogram.py: spectrum colour map, image cache and loader,
                      audio.py: note playback outputs - an external MIDI synth or the built-in one,
                      song.py: the audio file streamed to Qt's audio output)
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
| Transport | **Playback** (rewind, stop, play from the beginning, play/pause, forward, position readout), **Speed** (0.10x-2.00x in 5% steps with a `1.0` reset, pitch unchanged), **Tempo** (BPM, plus the estimated tempo of the audio), **Latency** (ms) |
| Edit | **Tools** (edit mode, pen, select, snap grid, clear), **Division** (note icon = the grid follows the beats of the tempo map, clock icon = it follows seconds) |
| Mix | **Spectrum** (gain, contrast), **Volume** (**Audio** for the file, **MIDI** for the notes) |

**Volume** has a slider for each layer: the audio file is streamed at the level of the first one, and
the second is the note playback - a scale factor for the built-in synth, and control change 7 (channel
volume) for an external one, which does its own mixing.

| Action | Input |
| --- | --- |
| Edit mode | The button in front of the tools: notes can only be drawn, moved, resized and selected while it is on, the spectrum behind them fades so that they stand out over it (WaveTone does the same), and picking the pen or the select tool turns the mode on as well - entering it starts on the pen, and the snap grid and the clear button are only usable inside it. The window opens with it off, so a click in the roll moves the playhead until a tool is picked. Hovering marks the row under the mouse, and the piano key with it, in either mode; editing adds the octave and the twelfth to both |
| Draw note | Pen tool: left drag on the empty grid. Horizontal movement sets the length, vertical movement sets the pitch, so the note follows the pointer |
| Move note(s) | Left drag a note |
| Resize note | Left drag either edge of a note, or Shift + left drag anywhere on it: its left half moves the start, its right half the end |
| Select note | Left click |
| Add to selection | Ctrl + left click |
| Box select | Select tool: left drag on the grid, or Ctrl + left drag with either tool |
| Select all | Ctrl + A |
| Delete | Right click a note, or Delete / Backspace for the selection |
| Cancel a drag | Escape |
| Play or pause | `Space` or the play/pause button |
| Move the playhead | A press anywhere in the roll - over the grid or over a note, in either mode - or a click in the timeline ruler (any mode, and it works while the file plays), or the rewind / forward buttons for the ends. A press on a note moves, resizes or selects it *and* moves the playhead. Dragging carries the playhead along with the pointer, and sounding every row it crosses like a glissando. While the audio plays the roll keeps its cursor so editing does not jump the sound; seeking then is what the ruler is for, and the file carries on from there |
| Double / halve the tempo | Right-click the Tempo field, or press `*` / `/` while it has the focus |
| Pan | Middle drag, a scrollbar, or drag in the timeline ruler to scroll horizontally |
| Zoom time | Ctrl + wheel |
| Zoom pitch | Ctrl + Shift + wheel |
| Scroll | Wheel along the timeline, Shift + wheel up and down the pitches |

The value sliders - **Speed**, **Gain**, **Contrast**, **MIDI** - land on the spot the track is clicked
and step with the wheel, one notch to a step, turning the value down as the wheel turns up.

Loading an audio file also estimates its tempo in the background (beat tracking plus a
least-squares fit over the beats, ~1 s for a whole song). It never changes the tempo by itself:
when it is done, the **Tempo** block shows a suggestion such as `≈93 BPM` next to the BPM field,
dimmed while few of its 12 s windows agree, with a tick to use it and a cross to drop it. Typing a
tempo, dropping the suggestion or loading another file discards it; the round arrow estimates again.
Changing the tempo never re-times the notes: they are timed against the audio, so only the beat
grid re-divides underneath them (and the roll keeps the audio at the same scale on screen, which is
how the notes stay where they are relative to the spectrum and to the time ruler).
The TempoCNN model (`namioto/tempo.py`, `namioto-tempocnn`) is kept as the runner-up: it is strong on
full mixes but its 256 integer-BPM classes and its training data (full mixes only) make it a poor
fit for stems, where beat tracking wins.

**Extracting notes with GAME.** A singing voice can be transcribed in one pass with
[GAME](https://github.com/openvpi/GAME)'s ONNX models:

```bash
uv run namioto-game song.wav --language zh          # fetches the small model on first use
uv run namioto-game song.wav --size medium          # one of small, medium, large
uv run namioto-game song.wav --quantize 4 --tempo 93 --midi out.mid
uv run namioto-game                                 # only fetch a model, do not transcribe
```

GAME is a PyTorch project and ships its models separately, in three sizes, so they are not packaged
here: `--size` picks one and it is downloaded from GAME's GitHub release into the data directory
(`~/.local/share/namioto/models/game/<size>`, via `platformdirs`), unpacked, and reused from then
on. An explicit `--model DIR` or `$NAMIOTO_GAME_MODEL` skips all of that and loads a directory as it
is.

| size | download | 20 s of a singing voice, 16-core CPU |
| --- | --- | --- |
| small | 46 MB | 1.9 s, 46 notes |
| medium | 180 MB | — |
| large | 362 MB | 7.1 s, 47 notes |

The notes come back as floating-point pitches with the onsets the model found; `--quantize` snaps
them to a beat grid first, and `--midi` writes them out. The code is MIT (Team OpenVPI, like GAME
itself); the models are CC BY-NC-SA 4.0, so anything produced with them is non-commercial, and they
are downloaded rather than redistributed here - see NOTICE.

The transport plays the loaded audio file and the notes on top of it. A press in the roll moves its
playhead wherever it lands - over a note it edits it as well, over the empty grid the pen draws one
there, so a click writes a note where the sound has just moved to. Held down, the drag keeps
carrying that playhead with the pointer and sounds every row it slides over, which is a glissando
up and down the keyboard. While the file is playing that
press only edits: the cursor is left alone, and the timeline ruler is what seeks, from where the
sound carries on. `Space` starts and pauses; the playhead, the readout and the timeline grid all
follow the file. The audio is streamed to Qt's audio output one buffer at a time, mono at the sample
rate of the file, so seeking is a cursor move. **Speed** rerenders the song with a phase vocoder
before it plays: the rhythm moves and the pitch does not, the way WaveTone's speed and pitch sliders
are separate. That rerender takes a couple of seconds for a whole song and runs in the background, so
the window stays live while the status bar says it is working. It also applies while the transport
runs: the notes are handed over at the new speed at once and the song picks it up when the rerender
lands, carrying on from where it had got to.

The notes go to a **software MIDI synth** when one is listening on the
MIDI bus - TiMidity and FluidSynth are recognised by name, and the tooltip of the **MIDI** slider
says which one is in use, because that is where the sound comes from (patches included). Without
one, the notes are rendered by a small additive synth inside the program and streamed through Qt's
audio output instead. **Latency** nudges the
position readout. A click in the roll auditions what it lands on - the pitch of the row, whether or
not it is a note - in either mode, and so does a key on the keyboard: listening and editing are
separate. Auditions never wait for the one before them: each click is its own note, and clicking the
same row again releases that note and starts it over - one note-off before the new note-on on the
synth, a 40 ms release ramp over the note still ringing inside - instead of stacking on it or waiting
for it to finish, so fast clicking on one row sounds every time. Hovering the roll tints the
row under the mouse and paints that piano key red, in
either mode, white or black; while editing it also tints the octave and the twelfth, and shows
the note name and frequency next to the status bar; outside edit mode the roll is a plain view of the
spectrum and the notes.

The **Snap** combo sets the quantisation applied when drawing, moving, and resizing notes, and
**Clear** removes every note. The **Division** buttons change how the time axis is divided — into
beats and bars of the tempo map, or into a 1-2-5 ladder of seconds — and nothing else: the ruler
shows the clock above the measure numbers under either of them.
