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

## Establish the song's grid and keep both GAME references

1. Measure BPM on the accompaniment or full mix. With that recording loaded, run `analysis.detect_bpm algorithm=wavetone` and poll `analysis.bpm_result job=ID`. Check the beat against passages near the beginning, middle and end, including half- and double-tempo candidates. A growing displacement calls for checking the tempo or a local tempo change before moving individual notes.
2. In the editing session, open the time-aligned vocal, wait for loaded audio in `state.get`, and set the confirmed BPM with `transport.set_bpm`. Run `analysis.transcribe` once and poll `analysis.transcription job=ID`. Retain the raw GAME attacks, releases and fractional pitches for comparison.
3. For the primary rhythmic reference, start with eighth notes. In the editor choose **1/8** and **Estimate phase**. Over IPC request `analysis.transcription_grid job=ID cells=2 estimate_phase=true` on the same run. Two cells per quarter note give a 250 ms cell at 120 BPM. Compare the returned grid and its quantized melody with the accompaniment and repeated sung phrases. Once confirmed, pass the returned `offset_ms` to `transport.set_grid_offset ms=VALUE`. The IPC grid request only previews the result. Keep the BPM, subdivision and applied offset with the raw and quantized results while editing.

## Decide the notes and lyric mapping together

Prepare the KRC readings and submit them with `lyrics.set`. Run `analysis.align` on the intended channel and poll `analysis.alignment job=ID`. Read each phrase with `analysis.phrase line=INDEX channel=CHANNEL` after running the models. It joins both alignments to the current sounds, raw GAME and the phase-estimated eighth-note grid. Check `missing` and request `spectrum=true` or `rms=true` for disputed passages. For Japanese lyrics, MMS and Yohane can disagree by a whole syllable or word. Use the following references phrase by phrase.

| Reference | Decision it supports |
| --- | --- |
| Phase-aligned GAME grid | Place ordinary attacks on the supported eighth-note grid and count cells across held notes. Small differences in a singer’s attack can share the same musical beat. Recheck conspicuous departures in the context of the phrase. |
| Raw GAME notes | Recover attacks or rests lost in quantization. Inspect clusters of short fragments around one vowel. A continuous pitch or vibrato with one attack usually belongs to one note. A distinct repeated attack can justify another note even at the same pitch. |
| Vocal spectrum and energy | Use `spectrum.frames start=SECONDS count=N` around disputed boundaries. Follow harmonic bands for pitch changes, successive attacks and voiced releases. Short-window RMS and a spectrogram help locate energy rises, rests and consonants. Breath, vibrato and instrumental bleed can all produce short GAME fragments. Compare the vocal with the mix and listen through both neighboring syllables. [Audio inspection script](scripts/inspect_audio.py) |
| Lyric alignment | Use the reading sequence to identify which sound owns each note. When a word falls far from the heard phrase, locate the last clear sound before it and the next clear sound after it, then resolve the intervening attacks in order. Check that the next word still maps correctly. |

Start with one note per sung sound. For example, if GAME splits a held vowel into three fragments and the spectrum shows continuous harmonics with no new attack or distinct pitch step, keep one note through its audible release. If the vowel carries two clear melodic steps, keep both pitches and give that sound two NOTE slots. Use a shared group for sounds sung together on one note and zero slots for an unsung unit. Preserve displayed punctuation and symbols such as ☆, ♥ and ♪, and check that the following sound still owns its intended note.

Keep the eighth-note grid for passages it explains. Inspect shorter candidates for leakage, unvoiced sounds and ornaments before assigning extra slots. Use a finer subdivision locally when repeated fast syllables or distinct melodic steps support it. Preserve a clearly performed pickup, delayed attack or early release where snapping would obscure the phrasing. Leave breathing gaps. For an echo or overlapping harmony, review the mapping on its own channel. [Backing vocals and sung responses](references/backing-vocals.md)

For a local revision, pass the phrase revision, ordered notes and sound groups to `document.apply_phrase`. Check the preview and next sound, then submit the original edit with `preview=false`. Read the phrase again on a conflict. [Phrase IPC example](../../docs/cli.md#review-and-edit-one-phrase). For a full initial import, `document.replace_notes` accepts the entire note list in seconds, including all channels. Save with `project.save`, wait for `subtitle.pending` to clear in `state.get`, then reopen and check the saved BPM and grid offset. Compare the KRC and MIDI from `project.export_source` with the reviewed notes, including short notes, shared groups, zero-count units and phrase handoffs.

## Mark vocal entrances with count-ins

Listen or inspect the vocal timeline for the first entrance and re-entries after instrumental breaks. Mark the pickup itself, even if it precedes the downbeat. With Namioto’s default template, place a KRC chapter separator (`---` on its own line) before each selected re-entry. Ordinary line and stanza breaks serve lyric phrasing. In the default double-row layout, the first main-vocal line of each chapter starts on the left and carries a lead cue when there is enough pre-roll. Check each re-entry in the rendered video, particularly after a chapter with an odd number of main-vocal lines.

Carry the confirmed BPM through export. For file exports, verify the MIDI tempo against the audio estimate; for session exports, verify the saved project tempo and exported MIDI. Use `--guide-dot-duration-ms 0` for automatic timing. Check saved settings and build scripts for a fixed millisecond override. In an external template, pass `song.bpm` to `prepare_tracks` and check available pre-roll with the actual cue preset’s `resolve_timing(bpm=song.bpm)`. For example, at 120 BPM the `--cue volume` preset flashes three times, once per beat, and fills its columns during the fourth beat. Audition the first entrance and each re-entry against the track, allowing for the chosen subtitle lead. File export supplies one song BPM; a tempo change needs section-specific timing in the template.

On the delivery render, inspect the start, middle and end of each planned count-in plus the first sung frame. Verify the symbols visibly progress toward the vocal entrance, clear the lyric on time in each requested edition. Preserve the singer’s note times when adding chapter or line breaks.

## Choose a template and render

Use the built-in default when the user gives no effect direction. For custom effects, author a project-local template using the default as a reference for timing, layout and cues. Select it explicitly for each output with `--script template.py --template RECIPE_ID`. Review the selected recipe’s parameters with `namioto-cli export --template-help` (add `--script` and `--template` for a trusted custom recipe). For palette, typography, layout, effects or a new builder, follow the kara-templater checkout’s `skills/kara-templater/SKILL.md`. [Template authoring skill](https://github.com/sitiyou/python-kara-templater/blob/main/skills/kara-templater/SKILL.md) · [Loading templates in Namioto](../../docs/karaoke-templates.md)

For a custom Namioto recipe, start from the [registration example](../../examples/cool_ink.py). Its script exposes `register(registry)`. Its builder receives `(song, values)` and returns a Scene. When adapting a kara-templater design, pass the song’s timed `song.sources` to the tracks so the export retains readings, voice roles and chapter cues.

For sing-along delivery, start with the sung highlight about 100 ms early. With the default template use `--offset-ms -100` at export. Apply this once, keeping project notes on the musical timeline. Set line preview and count-in duration separately so the singer can read the phrase in advance. Check the visible sweep against the final mix at an entrance, a fast handoff and a late verse. Tune the lead for comfortable anticipation. If only one word feels late, revisit its source mapping before shifting the whole subtitle.

Prefer a standalone libplacebo hook when the template and delivery path support it. Export at the delivery frame rate and render a short singing passage through the actual playback or encoding chain. Verify the time origin, aspect ratio, highlighted text, ruby and any shader effects before rendering the full song.

```sh
namioto-cli export --session --instance ID --out lyrics.hook --fps 60 --offset-ms -100
```

Replace the example frame rate with the target rate. Export ASS as a fallback when the template is ASS-only, the player requires ASS, or the target chain cannot render the hook correctly. Preview the fallback on its target renderer. A shared text-layout error can affect both backends and needs its own diagnosis.

If the user requests a rendered video, use their existing renderer or an available FFmpeg build. For a background video already containing the intended audio, one possible hook burn-in from the folder containing both files is:

```sh
ffmpeg -init_hw_device vulkan=vk -i "video.mp4" -vf "fps=60,libplacebo=custom_shader_path=lyrics.hook" -c:v libx264 -c:a aac "karaoke.mp4"
```

This example needs a working Vulkan device and FFmpeg with libplacebo, libx264 and AAC. Match the filter frame rate to the hook export. If the video and audio arrive separately, select the user's intended audio stream when assembling the video. Run the renderer directly; reuse a project build script when the user already has one.

For a finished video, verify stream start times, frame count, audio duration and representative rendered frames. If delivering vocal and instrumental versions, check that their accompaniment and lyric clocks agree.

For a requested pitch-lane edition, export the reviewed melody MIDI and follow the [pitch visualization workflow](references/pitch-visualization.md).
