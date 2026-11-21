---
name: namioto-nicokara
description: Author and verify timed karaoke lyrics with Namioto. Use its editor IPC to inspect vocal alignment, pitch and note mapping, save an editable project, and render a libplacebo hook or an ASS fallback.
---

# Author karaoke with Namioto

## Start with the files the user has

Work from the audio, lyric text and any MV or background footage supplied by the user. If an input needed for the requested deliverable is missing, ask the user to provide it. Search for or download source material only when separately requested. Identify the inputs and the requested deliverable before choosing tools. Use paths supplied by the user and create outputs in their chosen folder. A source recording and lyric text are enough to begin a new project. A finished subtitle also needs timed notes and a lyric-to-note mapping.

| Available input | Next step |
| --- | --- |
| An editable Namioto project (`.nto`) | Open it with its referenced audio available. Review its notes and lyric mapping, then export. |
| Mapped Namioto KRC lyrics and matching MIDI | Go straight to file export. For ordinary MIDI, run `uv run namioto-cli export --krc "song.krc" --midi "song.mid" --no-wavetone --out "lyrics.hook" --fps 60` from a configured source checkout. Omit `--no-wavetone` for MIDI with a WaveTone lead-in. |
| A recording and plain lyrics | Open the recording, prepare the lyrics with readings, and draw or import melody notes. Map the lyrics to those notes in the editor. If an isolated vocal and suitable models are available, use the analysis steps below to refine timing. |

A full mix can serve as the timeline for manual editing. Vocal separation is optional. Use an existing stem or a separator available to the user if the task calls for one. A backing track is necessary only for an instrumental edition. Background video and an encoder enter the workflow when the user asks for a finished video; an editable project or standalone subtitle can be delivered on its own.

## Select the recording and session

Use the final recording as the time reference. If a separate vocal is available, check its start and duration against that recording before aligning lyrics. When deriving a vocal from a mix and an official instrumental, measure their offset in several passages and check the residual during instrumental breaks. Different masters may require a vocal separation model.

For the packaged GUI, open the recording or project, use the lyrics editor to import or edit the KRC and review the mapping, then choose **Export karaoke…**. On Windows, extract the full release archive and launch `namioto.exe` from the extracted application folder. The release includes the editor; the command examples here use a [configured source checkout](../../docs/development.md#source-setup). See the [lyrics guide](../../docs/lyrics.md) for the manual route.

For agent control from a source checkout, prefix Namioto commands with `uv run`. Start `uv run namioto` in one terminal and issue control commands in another. These entry points work directly from PowerShell or a Unix shell. A project Makefile or a particular vocal separator is optional. List sessions with `namioto-ctl --list` and select one explicitly with `--instance ID`. Inspect `state.get` before editing. Use a fresh session for a new song. Use `project.open` to resume an existing project; `audio.open` starts a new document. Query `methods` to see what the running version supports.

## Prepare display lines in KRC

Plan display lines from the source wording before alignment. Read the phrase aloud and mark boundaries where a clause, a breath or a musical answer completes a thought. Keep words, names, ruby groups and short English phrases together. In Japanese, keep a particle with its phrase and avoid starting a line with closing punctuation. For example, 「窓を開けたら｜海の匂いがした」 gives the listener two complete phrases. Treat the bar here as an editorial break mark. Enter the chosen break as a lyric line boundary.

After reflowing a timed passage, verify the sung tokens, note ownership and highlight onsets across the new line boundary.

## Time lyrics against the voice

1. Prepare the lyrics and their sung readings in KRC. Check how Japanese kana, kanji readings, numbers and foreign words expand into natural sounds. Preserve printed punctuation and visible symbols such as ☆, ♥ and ♪ through transcription, KRC editing and export. Compare the displayed text with the source, including line endings. For an unsung mark, verify the rendered glyph and check that it consumes no note or shifts the following syllable.
2. For model-assisted alignment of a new project, open the isolated vocal with `audio.open`, wait for `state.get` to report loaded audio, then submit the KRC with `lyrics.set`. On a supported language and channel, start `analysis.align`. Poll `analysis.alignment job=ID` on the same instance. Its time pairs, scores and onset bounds follow the natural sounds on that channel. Save the evidence before starting another alignment.
3. Request melody evidence with `analysis.transcribe` and poll `analysis.transcription job=ID`. GAME returns onset and offset in seconds and fractional MIDI pitch. Compare uncertain boundaries against the note attacks, rests and neighboring phrases. For Japanese vocals, disagreements between MMS and Yohane are useful places to inspect. Use `spectrum.frames start=SECONDS count=N` for local pitch-band evidence. Instrumental leakage can trigger spurious notes.
4. Apply edits on the intended channel. For a newly built complete note list, `document.replace_notes` accepts pitch, start and duration in seconds. It replaces the whole list, so preserve other channels when editing an existing song. When one sound spans several notes, encode its NOTE count in the KRC. Round neighboring endpoints on a shared time grid before deriving durations.
5. Save the project with `project.save`. Wait for `state.get` to clear `subtitle.pending`. Use `project.export_source` to check the resulting KRC and MIDI. Reopen the saved project in another session and compare the exported source, particularly short notes at a non-default tempo.

When a passage includes an echo or harmony, map its performed notes on its own channel. For inline KRC wrappers, upper tracks and checking both voices against the audio, see [backing vocals and sung responses](references/backing-vocals.md).

## Optional tempo and grid references

When a phrase is hard to interpret, the accompaniment or mix can provide a rhythmic reference. Start `analysis.detect_bpm algorithm=wavetone` and poll `analysis.bpm_result job=ID`. Compare plausible candidates, including half or double tempo, against several passages. Set a project tempo with `transport.set_bpm` only when an editor grid is wanted.

For a completed GAME run, `analysis.transcription` returns the measured attacks. An optional `analysis.transcription_grid job=ID cells=4 estimate_phase=true` previews the same notes on a sixteenth-note grid at the editor's current BPM. It also estimates the subdivision offset. Compare a suspicious fragment with the vocal, lyric reading and surrounding notes. Preserve audible early or late entries, repeated attacks and sustained pitches. Listen to the accompaniment to identify a bar downbeat if needed. Applying a grid offset or replacing notes is a separate editing decision.

## Mark vocal entrances with count-ins

Listen or inspect the vocal timeline for the first entrance and re-entries after instrumental breaks. Mark the pickup itself, even if it precedes the downbeat. With Namioto’s default template, place a KRC chapter separator (`---` on its own line) before each selected re-entry. Ordinary line and stanza breaks serve lyric phrasing. In the default double-row layout, the first main-vocal line of each chapter starts on the left and carries a lead cue when there is enough pre-roll. Check each re-entry in the rendered video, particularly after a chapter with an odd number of main-vocal lines.

Set the guide step duration to fit the available pre-roll and the performed pickup.

On the delivery render, inspect the start, middle and end of each planned count-in plus the first sung frame. Verify the symbols visibly progress toward the vocal entrance, clear the lyric on time in each requested edition. Preserve the singer’s note times when adding chapter or line breaks.

## Choose a template and render

Use the selected recipe and review its parameters with `namioto-cli export --template-help` (add `--script` and `--template` for a trusted custom recipe). For palette, typography, layout, effects or a new builder, follow the kara-templater checkout’s `skills/kara-templater/SKILL.md`. [Template authoring skill](https://github.com/sitiyou/python-kara-templater/blob/main/skills/kara-templater/SKILL.md) · [Loading templates in Namioto](../../docs/karaoke-templates.md)

For a custom Namioto recipe, start from the [registration example](../../examples/cool_ink.py). Its script exposes `register(registry)`. Its builder receives `(song, values)` and returns a Scene. When adapting a kara-templater design, pass the song’s timed `song.sources` to the tracks so the export retains readings, voice roles and chapter cues.

Prefer a standalone libplacebo hook when the template and delivery path support it. Export at the delivery frame rate and render a short singing passage through the actual playback or encoding chain. Verify the time origin, aspect ratio, highlighted text, ruby and any shader effects before rendering the full song.

```sh
namioto-cli export --session --instance ID --out lyrics.hook --fps 60
```

Replace the example frame rate with the target rate. Export ASS as a fallback when the template is ASS-only, the player requires ASS, or the target chain cannot render the hook correctly. Preview the fallback on its target renderer. A shared text-layout error can affect both backends and needs its own diagnosis.

If the user requests a rendered video, use their existing renderer or an available FFmpeg build. For a background video already containing the intended audio, one possible hook burn-in from the folder containing both files is:

```sh
ffmpeg -init_hw_device vulkan=vk -i "video.mp4" -vf "fps=60,libplacebo=custom_shader_path=lyrics.hook" -c:v libx264 -c:a aac "karaoke.mp4"
```

This example needs a working Vulkan device and FFmpeg with libplacebo, libx264 and AAC. Match the filter frame rate to the hook export. If the video and audio arrive separately, select the user's intended audio stream when assembling the video. Run the renderer directly; reuse a project build script when the user already has one.

For a finished video, verify stream start times, frame count, audio duration and representative rendered frames. If delivering vocal and instrumental versions, check that their accompaniment and lyric clocks agree.

For a requested pitch-lane edition, export the reviewed melody MIDI and follow the [pitch visualization workflow](references/pitch-visualization.md).
