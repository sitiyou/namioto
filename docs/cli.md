# Command-line tools

[README](../README.md) · [Editor guide](usage.md) · [Lyrics](lyrics.md)

Examples below assume the source checkout and `uv sync --extra cpu` setup. Prefix commands
with `uv run` in that environment. `namioto-cli` runs tasks without a GUI; `namioto-ctl`
controls an already running editor.

```bash
uv run namioto-cli --help
uv run namioto-cli spectrum --help
```

Use each command's `--help` for its complete options.

## Spectrum and tempo

```bash
uv run namioto-cli spectrum song.mp3
uv run namioto-cli spectrum song.mp3 --bench
uv run namioto-cli spectrum song.mp3 --threshold 1.2
uv run namioto-cli wavetone song.mp3
uv run namioto-cli tempo song.mp3
uv run namioto-cli tempo song.mp3 --local
uv run namioto-cli tempo song.mp3 --json
uv run namioto-cli tempocnn song.mp3
```

Spectrum analyses 84 bands (C1–B7); `--bench` adds timings and `--threshold` adds detected
note spans. WaveTone's volume-envelope estimator is the editor default. `tempo` uses a
librosa beat tracker and least-squares fit; local estimates use 12-second windows, 6 seconds
apart. JSON output includes beats. TempoCNN works best on full mixes rather than stems.

## Extract singing-voice notes

```bash
uv run namioto-cli game song.wav --language zh
uv run namioto-cli game song.wav --size medium
uv run namioto-cli game song.wav --device gpu
uv run namioto-cli game song.wav --quantize 4 --tempo 93 --midi out.mid
uv run namioto-cli game
```

GAME downloads a model on first use; running without audio only fetches the model.

| Size | Download |
| --- | --- |
| small | 46 MB |
| medium | 180 MB |
| large | 362 MB |

Models are cached under the platform's data directory (`~/.local/share/namioto/models/game/<size>`
on Linux). `--model DIR` or `$NAMIOTO_GAME_MODEL` selects an existing directory instead.
`--quantize` snaps the inferred pitches and timing to a beat grid before MIDI export.

In the editor, the wand opens GAME options. **Target** selects a new or active channel,
with confirmation before overwriting notes. A completed run inserts its notes automatically.
Options and results are remembered; identical requests can reuse cached notes.

![GAME transcription setup in the editor](transcription.png)

## Devices and models

The initial `uv sync --extra cpu` installs ONNX Runtime for CPU analysis. Alternatively:

```bash
uv sync --extra cuda
# Or:
uv sync --extra webgpu
```

CUDA requires the GPU runtime, CUDA 12 and cuDNN 9. WebGPU uses Vulkan with the CPU runtime
and a provider plugin. CPU and CUDA builds conflict: select one, not both. The editor's
Settings → Devices chooses the backend used by `--device gpu`. If a GPU provider cannot
be created, ONNX Runtime reports it and continues on CPU.

Models are downloaded separately from this project's `models` release or GAME's release.
They are not included in builds. Configure a GitHub mirror or proxy in Settings → Network
if needed. Models have separate licenses, including non-commercial restrictions; see
[NOTICE](../NOTICE).

## Align a vocal

```bash
uv run namioto-cli align vocal.wav lines.json --out aligned.json
```

Input must be an isolated vocal and a JSON list of `{start, end, tokens}` windows. Tokens
are romanised kana, one per sound. Windows must already be approximately correct: the
aligner refines them, rather than finding lines throughout a song.

Output gives each token's onset, end and probability; unplaced tokens remain untimed.
Treat ends cautiously: an end is the next onset, so a final token may extend into a rest.
Diagnostics on stderr include `empty`, `nonmonotonic` and `collapsed`; review those windows
before trusting them.

The default model is Meta's MMS; `yohane` is a karaoke fine-tune. `--dir` or
`$NAMIOTO_ALIGN_MODEL` selects a converted model. See [development](development.md#model-conversion)
for conversion tools.

## Export karaoke

```bash
uv run namioto-cli export --krc song.krc --midi song.mid --out song.ass
uv run namioto-cli export --krc song.krc --midi song.mid --out song.ass --watch
uv run namioto-cli export --session --out song.ass --watch
uv run namioto-cli export --session --out song.ass --save-krc song.krc --save-midi song.mid
```

The output suffix selects standard ASS or a standalone libplacebo hook. For example:

```bash
namioto-cli export --session --template default --set layout=single --out song.hook --fps 60
mpv --vo=gpu-next --sub-auto=no --glsl-shader=song.hook video.mp4
ffmpeg -init_hw_device vulkan=vk -i video.mp4 -vf 'fps=60,libplacebo=custom_shader_path=song.hook' -c:a copy karaoke.mp4
```

Use the hook on video with the same aspect ratio and time origin. FFmpeg's filter frame rate must match the export rate. Fonts are resolved at export. Ordinary ASS uses the playback machine's fonts.
See [Python templates](karaoke-templates.md) for registration, parameters and script reloading.

File mode uses the mapping already in KRC. Session mode uses the editor's current mapping
and connects automatically to the first running editor with remote control enabled. Intermediate KRC/MIDI files are
written only when requested. Session CLI outputs are written on the CLI machine.

`--watch` builds immediately and rebuilds after changes settle. Invalid inputs leave the
last successful output intact. A disconnected session exits instead of selecting another
editor. Stop watching with `Ctrl+C`.

Settings resolve per field in this order:

1. Explicit template parameter (`--set key=value`), followed by the dedicated command-line option.
2. `NAMIOTO_ASS_` environment variable, using the uppercase field name.
3. Current GUI export setting, in session mode only.
4. Built-in default.

Saved user preferences are not a CLI fallback. For example, `--font-size` corresponds to
`NAMIOTO_ASS_FONT_SIZE`; spacing defaults to 5. `--channel-colors '0=FF0000,1=00FF00'`
selects red and green highlights. GUI and ctl exports ignore these environment variables.

With a kara-templater build that supports proportional metrics, the default
template accepts `--palt true` to use a horizontal font’s OpenType proportional
spacing for body and ruby. The exported ASS carries the measured positions.
Install the same font for playback on upstream libass.

File mode removes WaveTone's leading MIDI bar by default; pass `--no-wavetone` for ordinary
MIDI. Session mode follows the editor's compatibility setting.

## Remote control

Local remote control starts by default. `--no-ipc` disables it for one run; `--ipc` explicitly
enables it. The old Remote settings value is ignored.
Multiple editors may run at once; clients automatically select the first surviving instance
in startup order. Use `--list` to see instance IDs and `--instance ID` to select another editor.

```bash
uv run namioto-ctl status
uv run namioto-ctl --list
uv run namioto-ctl --instance INSTANCE_ID status
uv run namioto-ctl play
uv run namioto-ctl seek 12.5
uv run namioto-ctl bpm 132
uv run namioto-ctl notes
uv run namioto-ctl document.add_notes notes='[{"pitch":60,"start":0.5,"duration":1.0}]'
uv run namioto-ctl project.save path=song.nto
uv run namioto-ctl audio.open path=song.mp3 analysis='{"channels":"both"}'
uv run namioto-ctl project.export_lyrics path=song.ass
uv run namioto-ctl methods
uv run namioto-ctl --watch toggle
```

Parameters use `NAME=VALUE` with JSON types preserved. A short alias can take a single
positional value. `seek` also accepts `beat=8` or `fraction=0.5`. Calls that would discard
unsaved notes fail with `dirty` unless passed `force=true`.

`project.export_lyrics` writes on the editor's machine and accepts `save_krc=PATH` and
`save_midi=PATH`. Session export commands accept `--instance` and `--timeout` like ctl.

### Local transport and discovery

Endpoint names and discovery files are managed automatically and cannot be customized.
Unix systems use a separate domain socket per instance, with mode 0600 in a 0700 directory.
Windows uses a separate named pipe per instance, restricted to the user running the editor.
No TCP port is opened, and no token is needed.

Discovery records live under the app's runtime directory in `ipc/`, alongside Unix sockets.
With `XDG_RUNTIME_DIR` set, this is `$XDG_RUNTIME_DIR/namioto/ipc/`; otherwise the app's
platform-specific configuration directory is used. Normal exit removes the instance's endpoint
and record; discovery removes stale records and Unix sockets left by exited processes.
Clients do not need to know these paths. An absent editor or unknown instance ID produces an error.

### Protocol

Messages are JSON objects, one per line:

```json
{"id":1,"method":"transport.play"}
{"id":1,"ok":true,"result":{}}
{"id":1,"ok":false,"error":{"code":"dirty","message":"Unsaved changes"}}
{"event":"state","data":{}}
```

These illustrate request, success, error and pushed-event shapes. Watchers receive events
without polling. Python integrations can use `namioto.ipc.Client`; query `methods` for the
running editor's methods and parameters.

For timing a vocal by remote control, load the separated voice and set the project KRC.
Start a measurement with `namioto-ctl analysis.align model=yohane channel=0`, then poll
`namioto-ctl analysis.alignment job=ID` on the same instance. The editor keeps the latest 32 GAME and lyric-alignment jobs in memory, keyed
by job ID, until remote control stops. Each alignment records its audio path,
file stamp, channel and lyric signature. A completed job
returns one row per lyric line on the selected channel, with start/end pairs in seconds,
scores and onset uncertainty bounds per natural sound. A failed job includes the error.
The caller can inspect this evidence before editing notes. For the underlying piano-roll
data, `namioto-ctl spectrum.frames start=16.25 count=24` returns up to 256 consecutive frames.
Rows begin at the returned audio time, are spaced by `step` seconds and start at MIDI
`first_pitch`. Gain, contrast and visual latency do not affect these amplitudes.

For melody evidence, start `analysis.transcribe size=large language=ja` on the current
vocal. Poll `analysis.transcription job=ID` for GAME's raw note list (onset and offset in
seconds, fractional MIDI pitch). Both analysis commands leave the project's notes untouched.

Run `analysis.detect_bpm algorithm=wavetone` on the mix or accompaniment and poll
`analysis.bpm_result job=ID`. The result includes the BPM, WaveTone's ranked
(BPM, score) candidates and distribution, or the window agreement and residual
available from the other backends. To compare algorithms, save each result before
starting another job with `librosa` or `tempocnn`. Select a tempo explicitly with
`transport.set_bpm bpm=VALUE` in the project where the notes will be edited.

After a GAME job completes, call `analysis.transcription_grid job=ID cells=4 estimate_phase=true`
to preview sixteenth notes at the editor's current BPM. The result contains snapped
notes in seconds and the proposed grid offset in milliseconds. Repeat on the same
job to compare subdivisions. Without phase estimation it uses the current editor
offset. Apply a chosen offset with `transport.set_grid_offset ms=VALUE`; the
original GAME result remains available through `analysis.transcription`.

### Review and edit one phrase

After running GAME or a lyric aligner, call `analysis.phrase line=0 channel=0`.
The line index counts displayed KRC lines from zero across chapters. The result
includes the line's notes in seconds, stable note IDs, KRC mapping groups and the
readings of the surrounding sounds. `context` defaults to one sound on either
side in the same voice. Context note IDs resolve through `context_notes`.

The result uses the latest completed GAME job for the recording and the latest
matching MMS and Yohane jobs for that channel's lyrics. It returns both GAME
note lists over the phrase window, the estimated and current editor grid
offsets, and the model times, scores and onset bounds for each sound. The
default grid has two cells per beat (eighth notes). Missing evidence is listed
in `missing`. With no mapped or aligned times, supply `start` and `end` in
seconds to select the audio interval.

Request `spectrum=true` for the editor's note bands or `rms=true` for 20 ms
linear RMS values per source audio channel. These optional arrays have at most
256 frames each. For a longer interval, request the next page using that
array's `next_start` and the same end time.

To review a mapping change, pass the line's `revision`, its ordered `notes` and
its `mapping` to `document.apply_phrase`. The default is a read-only preview
showing both versions and the next sound's note ownership. With a connected
Python IPC client, for example, split a sustained vowel on a one-sound,
one-note first line after confirming the second pitch in the recording:

```python
row = client.request("analysis.phrase", {"line": 0, "context": 0})
note, = row["notes"]
sound, = row["sounds"]
half = note["duration"] / 2
edit = {
    "line": 0,
    "revision": row["revision"],
    "notes": [
        {**note, "duration": half},
        {"start": note["start"] + half, "duration": half, "pitch": note["pitch"] + 2},
    ],
    "mapping": [{"sounds": [sound["index"]], "slots": 2}],
}
preview = client.request("document.apply_phrase", edit)
# After reviewing the preview, submit the original request with preview disabled.
client.request("document.apply_phrase", {**edit, "preview": False})
```

Keep the ID when revising an existing note and omit it for a new note.
Groups cover the selected voice's sound indices in order. A group with zero
slots consumes no note, multiple sounds with one slot share a note, and one
sound with multiple slots consumes consecutive notes. An edit that changes
another line's note ownership is rejected. A stale revision returns
`conflict`; read the phrase again before revising it. A committed edit of both
notes and mapping takes one editor undo step.
