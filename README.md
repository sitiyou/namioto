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
uv run namioto song.nto                   # open a project (the notes and the audio together)
uv run namioto song.mp3 --channels both --gain 300   # analysis options
uv run namioto-tempo song.mp3             # estimate the tempo of a file (beat tracking + fit)
uv run namioto-tempo song.mp3 --local     # per-window estimates, 12 s wide, 6 s apart
uv run namioto-tempo song.mp3 --json      # machine readable, includes every beat
uv run namioto-tempocnn song.mp3          # the same with the TempoCNN model (runner-up)
uv run namioto-spectrum song.mp3          # analyse into 84 note bands (C1-B7)
uv run namioto-spectrum song.mp3 --bench  # plus per-stage timings
uv run namioto-spectrum song.mp3 --threshold 1.2   # plus auto-filled note spans
uv run namioto-game song.mp3 --model DIR  # extract the notes of a singing voice (GAME's models)
uv run namioto-align vocal.wav lines.json # time known lyrics against a separated vocal
uv run pytest                             # tests (the beat tracker's slow ones left out)
uv run pytest -m slow                     # just those, minutes of synthetic audio
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

## Projects

The **Project** block of the transport row holds `Open` and `Save` (`Ctrl+O`, `Ctrl+S`, `Ctrl+Shift+S`
for Save As), and `Export MIDI` beside them for a MIDI file of its own. A `.nto` project is a plain
JSON file that keeps the notes together with what they were drawn over and the values that belong to
that piece of work: the audio file, the tempo, the analysis parameters, the spectrum display, the snap
grid and the view. It is a few kilobytes, so it is diffable, searchable and editable by hand.

Opening an audio file is how a project starts: namioto asks where its `.nto` goes, defaulting to the
audio's own name beside it, and writes it there. A MIDI file is imported into the project that is
open, never opened on its own, and switching to another project or another audio file asks about
unsaved notes first.

Notes are kept in seconds, so a different tempo moves the grid and never the notes, and the file
lists them in time order. The audio is recorded as a path - relative to the project when it sits
beside it - and never copied into the project. A project whose audio is missing still opens, since
the notes are worth having, and says so in the status bar.

The window title shows the project name, with a `*` while there are unsaved changes; closing, or
opening another project, asks before they are lost. Moving the view, or turning a volume down, is
written when you save but does not count as a change, so looking around never nags. A roll that has
not been saved under a name yet is treated as a sketch and closes without asking.

Settings and projects stay apart: the values inside a project belong to that project and never
overwrite the program's own defaults, which are what the next file starts from.

## MIDI files

`Open` (`Ctrl+O`) takes audio files and `.nto` projects — the suffix picks whether the file is
analysed behind the roll or opened as a project — and, once a project is open, `.mid`/`.midi` files
to import into it: a MIDI is a part of a project and is never opened on its own. `Export MIDI`
writes the roll out for a DAW, a score program or a synth, saving the project
separately. A MIDI is read by channel: every MIDI channel with notes in it becomes one channel here,
carrying its instrument and channel volume, and one track chunk holding several channels is read as
several. A channel carries no name -
a name belongs to a track chunk, which may hold any number of channels - so names live in the project
file only, and none is read from or written to MIDI. Importing into a window that already has audio
loaded keeps the audio, the view and the snap grid, which is how a transcription made elsewhere is
checked against the sound it came from; what it cannot use (a note that never ends, a channel past the
sixteenth, a tempo change after the first, since the grid holds one tempo) is counted in the
status bar rather than dropped silently. An import over a roll that already has notes asks first, and
along with **Replace** it offers **Merge**: each of the file's channels is pointed at one of the
roll's channels or at a new one. The file's i-th channel starts on the roll's i-th channel while that
one carries no notes, and the ones whose place is taken fill the empty channels left, in order, so
the notes arriving are added to a channel of their own rather than mixed into one already in use; the
mapping is edited in the same dialog, and the tempo stays where the audio put it.

**Export MIDI** asks for one thing only, the file name, and writes every channel on the project's own
tempo, one track chunk per channel and no name on any of them. A note drawn on the roll's grid lands
exactly on the tick that grid names, and one taken from the audio keeps the time it has, rounded to
the nearest tick; a hidden channel goes in like any other, since hiding is about the drawing and not
the notes. **WaveTone compatibility**, in the settings, is
on by default: WaveTone's own MIDI export starts every note one bar late, so a file it wrote is read
back with that bar removed, and a file written here carries it again - which is what its own tools
expect. Turn it off to exchange plain MIDI with anything else,
since a file whose notes start before that bar was never WaveTone's and is read as it stands.

## Settings

The gear at the right end of the Mix row opens the settings window, and it is short on purpose: it
holds only what has no control in the bars - the two windows the beat tracker fits, **General** (the
interface **Language**, where the default `Follow system` takes the machine's own and a change takes
effect on the next run, **Auto-save**, off by default, which writes the open project once editing
stops and when the window loses focus, and **Style**, the widget style that draws the window),
**WaveTone compatibility**, and **Lyrics** (the OpenAI-compatible endpoint the lyrics window may
call - **API base** up to its `/v1`, **API key**, **Model**, the temperature and timeout under an
`ADVANCED` heading - and the **External editor** command a `.krc` is opened with) - a page per
group. `Restore defaults` puts everything back, and `Apply` lets the change go live without closing
the window.

![settings](docs/settings.png)

Everything else is remembered rather than configured. The style, the gain, the contrast, the audio
and MIDI volumes, the speed, the snap grid, the division, the zoom, the two switches beside the
transport readout and the window's own size and position are written to
`~/.config/namioto/settings.json`
(`$NAMIOTO_SETTINGS` points somewhere else) as they change on screen, and are back the way they were
next time. The tempo and the grid offset are not among them: they describe one song, so they start from
their defaults (120 BPM, 0 ms) whenever another file is loaded, and a project carries them - opening
it puts its own back. The file is plain JSON, so it can be edited by hand - and a hand-mangled or
half-written one falls back to the defaults field by field instead of refusing to start.

The command line still wins for one run: `--channels`, `--t-num`, `--gain` and `--contrast` shape
this run alone and are never written back into the file.

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
namioto/channels.py   the MIDI channels a note plays on, and the values they play with, no Qt
namioto/interaction.py the roll's normal/edit mode and its tool, as one value, no Qt
namioto/document.py   the notes and the MIDI channels they play on, in beats, no Qt
namioto/midi.py       reading and writing MIDI files (mido), no Qt
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
| Transport | **Project** (open, save, export MIDI), **Playback** (rewind, stop, play from the beginning, play/pause, forward, position readout, and the four display switches: auto page turn, overtone highlight, the channel sidebar, and the time division - the metronome icon checked means the time axis follows the beats of the tempo map, unchecked the seconds), **Speed** (0.10x-2.00x in 5% steps, pitch unchanged, with a reset icon back to 1.00x), **Tempo** (BPM, the estimated tempo of the audio, and the grid offset in ms) |
| Edit | **Tools** (edit mode, pen, select, snap grid, quantize, the GAME transcription, the lyrics importer) |
| Mix | **Spectrum** (gain, contrast), **Volume** (**Audio** for the file, **MIDI** for the notes), and the gear that opens the settings window (the few options the bars do not hold; the analysis parameters belong to the project) |

**Volume** has a slider for each layer: the audio file is streamed at the level of the first one, and
the second is the note playback - a scale factor for the built-in synth, and control change 7 (channel
volume) for an external one, which does its own mixing.

**Channels** are toggled by the layers icon among the playback switches: a sidebar with one card per MIDI channel.
The notes of each channel are painted in its colour, and the card holds the name (double-click to
rename), the GM instrument the channel plays, and the lock, show and mute switches. Clicking a card
makes it the drawing channel; the context menu adds a channel, sets its volume or deletes it (the
last one stays). Deleting one leaves the numbers of the others alone, so a note keeps the MIDI
channel it plays on. Any of the sixteen can be used, percussion included.
A locked channel cannot be edited, a hidden one is not drawn, and a muted one stays silent - the
built-in synth plays each channel with the voice of its instrument family, an external one receives
the real GM program on the channel's own number.

| Action | Input |
| --- | --- |
| Edit mode | The button in front of the tools: notes are drawn only while it is on (WaveTone keeps its graph to the spectrum outside note edit mode), and only then can they be drawn, moved, resized and selected; the spectrum behind them fades so that they stand out over it (WaveTone does the same), and picking the pen or the select tool turns the mode on as well - entering it starts on the pen. The snap grid and the quantize button are the two that work in either mode: they set the grid and apply it, and the notes they move are drawn once the mode is on. The window opens with it off, so a click in the roll moves the playhead until a tool is picked. Hovering marks the row under the mouse, and the piano key with it, in either mode; with **Overtone highlight** on, the overtones of that row - `f`, `2f`, `3f` and `4f` - are marked the same way, in either mode, as WaveTone does |
| Draw note | Pen tool: left drag on the empty grid. Horizontal movement sets the length, vertical movement sets the pitch, so the note follows the pointer |
| Move note(s) | Left drag a note, which steps in whole snap cells and keeps where it sits inside its cell; Quantize is what lands it on the grid |
| Resize note | Left drag either edge of a note, or Shift + left drag anywhere on it: its left half moves the start, its right half the end. A note is never left shorter than one snap cell |
| Select note | Left click; a click on one that is already selected leaves only that one, while a press that drags carries the whole selection |
| Add to selection | Ctrl + left click |
| Box select | Select tool: left drag on the grid, or Ctrl + left drag with either tool |
| Select all | Ctrl + A |
| Delete | Right click a note, or Delete / Backspace for the selection |
| Copy notes | `Ctrl+C` takes the selected notes as one block, timed from their earliest note |
| Paste notes | `Ctrl+V` drops that block at the playhead, its first note on the snap grid and the spacing between them quantised to the same cell, and selects what it pasted |
| Quantize notes | The grid icon beside the Snap combo, in edit mode: the starts and ends of the notes land on the snap grid, so they sit on the beats of the current tempo. The selection when there is one, the whole roll otherwise; a locked channel is never touched, and a note shorter than one cell is left one cell long |
| Cancel a drag | Escape |
| Play or pause | `Space` or the play/pause button |
| Open a project | `Ctrl+O`, or `Open` in the Project block |
| Save a project | `Ctrl+S` (Save As on the first save, or `Ctrl+Shift+S`), or `Save` in the Project block |
| Export MIDI | `Export MIDI` in the Project block, next to `Save` |
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
dimmed while few of its 12 s windows agree, with a tick to use it and a cross to drop it. The balloon
floats inside the window, so it takes neither the keyboard nor the click: drawing, selecting or
playing underneath it goes on, and typing a tempo, dropping the suggestion or loading another file
discards it; the round arrow estimates again.
Changing the tempo never re-times the notes: they are timed against the audio, so only the beat
grid re-divides underneath them (and the roll keeps the audio at the same scale on screen, which is
how the notes stay where they are relative to the spectrum and to the time ruler).
The TempoCNN model (`namioto/tempo.py`, `namioto-tempocnn`) is the runner-up, on its own command
line: it is strong on full mixes but its 256 integer-BPM classes and its training data (full mixes
only) make it a poor fit for stems, where beat tracking wins.

**Extracting notes with GAME.** A singing voice can be transcribed in one pass with
[GAME](https://github.com/openvpi/GAME)'s ONNX models:

```bash
uv run namioto-game song.wav --language zh          # fetches the small model on first use
uv run namioto-game song.wav --size medium          # one of small, medium, large
uv run namioto-game song.wav --provider cuda        # on an NVIDIA GPU, with ONNX Runtime's GPU build
uv run namioto-game song.wav --quantize 4 --tempo 93 --midi out.mid
uv run namioto-game                                 # only fetch a model, do not transcribe
```

The editor can do the same over the file it has open: the wand button in the tools opens a
window with GAME's options (model size, backend, language, the quantisation grid and its inference
parameters), runs the model in a process of its own so a crash cannot take the editor down, and
watches it there with a progress bar and a log. The notes arrive on a channel of their own, or over
the active channel's own — the window's **Target** says which, and asks before overwriting a channel
that already holds notes. A finished run inserts itself, so there is no second click to make. The
options are remembered for the next run, and so is the result: asking for exactly the same run again
offers the saved notes instead of loading the model a second time.

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
them to a beat grid first, and `--midi` writes them out. The **Backend** option (the dialog's own
field, `--provider` on the command line) picks where the models run: `cpu` by default, or `cuda` for
an NVIDIA GPU, which needs ONNX Runtime's GPU build together with CUDA 12 and cuDNN 9. A CUDA
provider that cannot be created is not an error - ONNX Runtime says so on stderr and the run carries
on the CPU. The code is MIT (Team OpenVPI, like GAME
itself); the models are CC BY-NC-SA 4.0, so anything produced with them is non-commercial, and they
are downloaded rather than redistributed here - see NOTICE.

## Importing lyrics

A project can carry rubies for the words its notes sing, in a `.krc` file beside it - the same
base name with the `.krc` suffix. The text-box button beside the GAME wand opens a window over that
file: paste the lyrics into the upper box (or load a `.txt`, `.md` or `.lrc`), and either ask a
model to annotate it or type the lower one by hand. `Save` writes the `.krc` as plain UTF-8 text -
readable and editable on its own - and the editor reloads it whenever something else changes it, so
a hand edit shows up without reopening the project. `Open in external editor` hands the file to the
command named under **External editor** in the settings, or to the platform's own choice when that is
left empty.

The prompt is built in and adds the rubies: it is the same rule set an external `lyrics.md` role would
carry, asking for the kana of each kanji in square brackets, grouped per word and comma-separated.
`Copy prompt` puts it on the clipboard together with the lyrics, for a web model - paste it there,
paste the answer back into the lower box and save. With an OpenAI-compatible endpoint set up under
**Lyrics** in the settings, `Translate with the API` does that round trip in the background instead,
and a box under the result streams the model's own output as it arrives - its reasoning first, then
the answer. That box appears only once the API is asked for; the clipboard and hand-editing routes
never need it. The key lives in `~/.config/namioto/settings.json` in plain text, and nothing here
checks the `.krc` syntax: a file with a mistake in it is still one you can fix in an editor.

Once a `.krc` is open and its audio is loaded, the clock button beside the text-box one puts a time
on every mora: the whole stream is forced onto the frames of a wav2vec2 CTC model in one pass, and
each mora gets the span of frames it won. The lyrics are drawn above the roll as one row of
note-like blocks, on the roll's own columns; a block is green while its mora sits on a note and red
while it does not. Drag a block to move it or an edge to trim it, and it snaps onto the note grid; a
line's blocks keep the order they were read in and never overlap, so a block dragged by its middle
needs the room to be free while an edge dragged into a neighbour takes the room from it. A drag is a
preview: come back to where you started before letting go and nothing changes, not even an undo step.
A dragged block steps in whole cells too, keeping where it sits inside its cell.
Drag a block up or down and its length goes away: a mora nothing is sung on, its block no longer drawn and no
longer standing in the way of the blocks around it. The menu
over the block it follows puts it back, out of the room in front of it or out of that block's own
space. Click a block to select it, Ctrl-click to add or drop one, Shift-click for everything from
the last click to the pointer, and a click on a block that is already selected leaves only it; a
press that drags carries them all. A selected block wears a yellow rim (`theme.LYRIC_SELECT`) over a body
that keeps saying whether its mora sits on its notes, and dragging one of the blocks moves them all
together. Right-click a run of them and they can be made one word: the `.krc` gains `(...)`, and the
run counts as one word from then on, so its `.N` is the notes the whole run covers - コー, two morae
of one sound, shares the one note under it rather than one of them being squeezed away.
The times ride in the `.nto` beside the notes, saved and undone with them.
Exporting MIDI checks the morae against the notes first - a mora has to start and end on the notes
it covers, though it may cover several with gaps between - and writes each mora's note count back
into the `.krc` as its `.N`. The align window also offers a Quantize choice (Off / 1/4 / 1/8 / …)
that snaps the result onto the BPM beat grid, and remembers the model, the device and the Quantize
choice for the next run. A finished run is kept as a cache, so changing the
tempo or the grid offset and running again re-snaps it without touching the model.

## Aligning lyrics to a vocal

Given a separated vocal and the tokens of each line, `namioto-align` puts a time on every token: the
tokens are forced onto the frames of a wav2vec2 CTC model, which never recognises anything, it only
says where the words it is given fall. The vocal has to be the isolated singing voice and not the
mix, the tokens its kana romanised, and each line a roughly right window - a list of
`{start, end, tokens}` in a JSON file, one token per mora - because the aligner refines a window and
cannot find one: it is passed as a whole, only the model's own frames carry the times.

```bash
uv run namioto-align vocal.wav lines.json --out aligned.json
```

Each line comes back with one entry per input token - its onset, its end and the model's probability
- and a token the model could not place stays untimed. Only the onset is worth reading: a token's end
is the next token's onset, so the last one before a rest reaches into the rest. Lines the run could
not place are named on stderr: `empty` when nothing was timed, `nonmonotonic` when the times run
backwards, `collapsed` when a run of tokens was squeezed into no time at all, and `diverged` against
reference times handed to the module's `problems()`. A line wearing one of them is worth running
again with another window before it is believed.

`namioto.utils.kana_tokens` turns a line's kana into those tokens, one hepburn token per mora. The
model is not redistributed and has no download of its own: `scripts/export_align_model.py` converts
one into the data directory once, and `--dir` or `$NAMIOTO_ALIGN_MODEL` points at a converted one.
Two models can be exported: `mms` (Meta's MMS forced-alignment checkpoint, the default) and `yohane`
(the karaoke fine-tune `NextFire/mms-300m-ForcedAligner-karaoke-ja-Latn`).

```bash
uv run --group export scripts/export_align_model.py --model mms
uv run --group export scripts/export_align_model.py --model yohane
```

The script is a development tool and needs torch (plus torchaudio for `mms`, transformers for
`yohane`) and ONNX, which the program itself does not install.

The transport plays the loaded audio file and the notes on top of it. A press in the roll moves its
playhead wherever it lands - over a note it edits it as well, over the empty grid the pen draws one
there, so a click writes a note where the sound has just moved to. Held down, the drag keeps
carrying that playhead with the pointer and sounds every row it slides over, which is a glissando
up and down the keyboard. While the file is playing that
press only edits: the cursor is left alone, and the timeline ruler is what seeks, from where the
sound carries on. `Space` starts and pauses; the playhead, the readout and the timeline grid all
follow the file. The audio is streamed to Qt's audio output one buffer at a time, mono at the sample
rate of the file, so seeking is a cursor move. **Speed** stretches the song with a phase vocoder as it
streams: the rhythm moves and the pitch does not, the way WaveTone's speed and pitch sliders are
separate. Only the samples the output asks for are generated, so a speed change costs a reset rather
than a rerender of the whole song, and it applies while the transport runs too - the notes and the
song both pick the new speed up at once, carrying on from where they had got to.

The notes go to a **software MIDI synth** when one is listening on the
MIDI bus - TiMidity and FluidSynth are recognised by name, and the tooltip of the **MIDI** slider
says which one is in use, because that is where the sound comes from (patches included). Without
one, the notes are rendered by a small additive synth inside the program and streamed through Qt's
audio output instead. **Grid offset (ms)** slides the grid lines - `-` draws them to the left, `+` to
the right, and snapping follows them - while the notes and the playback keep their exact
timestamps. A click in the roll auditions what it lands on - the pitch of the row, whether or
not it is a note - in either mode, and so does a key on the keyboard: listening and editing are
separate. Auditions never wait for the one before them: each click is its own note, and clicking the
same row again releases that note and starts it over - one note-off before the new note-on on the
synth, a 40 ms release ramp over the note still ringing inside - instead of stacking on it or waiting
for it to finish, so fast clicking on one row sounds every time. Hovering the roll tints the
row under the mouse and paints that piano key red, in
either mode, white or black; with **Overtone highlight** (the wave icon, off by default) on it also
tints the overtones of that row - `f`, `2f`, `3f` and `4f` - while editing or not, and shows the note
name and frequency next to the status bar; the notes are drawn only while edit mode is on, so outside it
the roll is a plain view of the spectrum.

The **Snap** combo sets the quantisation applied when drawing, moving, and resizing notes; the grid icon beside it quantises the notes already on the roll onto that same grid, which is how an imported MIDI or a transcription made with another tempo is brought onto the beats here. The **Division** icon changes how the time axis is divided — into
beats and bars of the tempo map, or into a 1-2-5 ladder of seconds — and nothing else: the ruler
shows the clock above the measure numbers under either of them.
