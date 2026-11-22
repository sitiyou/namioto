---
name: namioto-nicokara
description: Author and verify timed karaoke lyrics with Namioto. Use its editor IPC to inspect vocal alignment, pitch and note mapping, and deliver editable timing sources for kara-templater.
---

# Author karaoke with Namioto

Namioto owns the editable notes, readings, chapter boundaries and lyric-to-note mapping. Its deliverable in this workflow is a timed song input. All visual templates, including the basic design, run in the kara-templater stage.

## Start with the files the user has

Work from the audio, lyric text and any MV or background footage supplied by the user. If an input needed for the requested deliverable is missing, ask the user to provide it. Search for or download source material only when separately requested. Identify the inputs and the requested deliverable before choosing tools. Use paths supplied by the user and create outputs in their chosen folder. A source recording and lyric text are enough to begin a new project. A finished subtitle also needs timed notes and a lyric-to-note mapping.

| Available input | Next step |
| --- | --- |
| An editable Namioto project (`.nto`) | Open it with its referenced audio available. Review its notes and lyric mapping, then save the timing handoff. |
| Mapped Namioto KRC lyrics and matching MIDI | Verify the recording, MIDI lead-in convention and lyric mapping, then prepare the song input below. |
| A recording and plain lyrics | Open the recording, prepare the lyrics with readings, and draw or import melody notes. Map the lyrics to those notes in the editor. If an isolated vocal and suitable models are available, use the analysis steps below to refine timing. |

A full mix can serve as the timeline for manual editing. Vocal separation is optional. Use an existing stem or a separator available to the user if the task calls for one. A backing track is necessary only for an instrumental edition. Background video and an encoder enter the workflow when the user asks for a finished video; an editable project or standalone subtitle can be delivered on its own.

## Select the recording and session

Use the final recording as the time reference. If a separate vocal is available, check its start and duration against that recording before aligning lyrics. When deriving a vocal from a mix and an official instrumental, measure their offset in several passages and check the residual during instrumental breaks. Different masters may require a vocal separation model.

For the packaged GUI, open the recording or project, use the lyrics editor to import or edit the KRC and review the mapping, then save the editable project. On Windows, extract the full release archive and launch `namioto.exe` from the extracted application folder. The release includes the editor; the command examples here use a [configured source checkout](../../docs/development.md#source-setup). See the [lyrics guide](../../docs/lyrics.md) for the manual route.

For agent control from a source checkout, prefix Namioto commands with `uv run`. Run agent-owned IPC editors headlessly as a background process with the Qt platform set to `offscreen` in that process only (POSIX example: `QT_QPA_PLATFORM=offscreen uv run namioto --ipc >namioto-ipc.log 2>&1 &`). Select the newly started instance and confirm the loaded project with `state.get`. File-mode CLI analysis can run directly without an editor. These entry points work directly from PowerShell or a Unix shell. A project Makefile or a particular vocal separator is optional. List sessions with `namioto-ctl --list` and select one explicitly with `--instance ID`. Inspect `state.get` before editing. Use a fresh session for a new song. Use `project.open` to resume an existing project; `audio.open` starts a new document. Query `methods` to see what the running version supports.

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
| Lyric alignment | Use MMS for sound ownership and candidate attacks only. Never use its end timestamps to estimate, corroborate or break ties on NOTE releases. Use the reading sequence to identify which sound owns each note. When a word falls far from the heard phrase, locate the last clear sound before it and the next clear sound after it, then resolve the intervening attacks in order. Check that the next word still maps correctly. |

Start with one note per sung sound. For example, if GAME splits a held vowel into three fragments and the spectrum shows continuous harmonics with no new attack or distinct pitch step, keep one note through its audible release. If the vowel carries two clear melodic steps, keep both pitches and give that sound two NOTE slots. Use a shared group for sounds sung together on one note and zero slots for an unsung unit. Preserve displayed punctuation and symbols such as ☆, ♥ and ♪, and check that the following sound still owns its intended note.

Keep the eighth-note grid for passages it explains. Inspect shorter candidates for leakage, unvoiced sounds and ornaments before assigning extra slots. Use a finer subdivision locally when repeated fast syllables or distinct melodic steps support it. Preserve a clearly performed pickup, delayed attack or early release where snapping would obscure the phrasing. Leave breathing gaps. For an echo or overlapping harmony, review the mapping on its own channel. [Backing vocals and sung responses](references/backing-vocals.md)

When a wipe jumps forward and then stalls, inspect adjacent rendered frames around the reported song time, accounting for the export lead. Check both the attack and the duration of each affected NOTE. A short CTC token window can identify a vowel inside a much longer sung note. Estimate the release independently on the vocal spectrogram. Follow the harmonic decay and compare it with raw GAME note-offs and surrounding vocal energy. Carry a continuous gesture into the next attack across short consonant closures. Preserve an audible release followed by a distinct re-entry, including within a lyric line. Extend through the supported release, preserving audible rests and the next attack. During initial import, flag very short notes followed by long gaps within a voiced phrase; inspect the audio before changing them. Recheck the saved MIDI and the same frames in the delivery renderer.

For a local revision, pass the phrase revision, ordered notes and sound groups to `document.apply_phrase`. Check the preview and next sound, then submit the original edit with `preview=false`. Read the phrase again on a conflict. [Phrase IPC example](../../docs/cli.md#review-and-edit-one-phrase). For a full initial import, `document.replace_notes` accepts the entire note list in seconds, including all channels. Save with `project.save`, wait for `subtitle.pending` to clear in `state.get`, then reopen and check the saved BPM and grid offset. Compare the KRC and MIDI from `project.export_source` with the reviewed notes, including short notes, shared groups, zero-count units and phrase handoffs.

## Hand off the timed song to kara-templater

Mark the first entrance and re-entries after instrumental breaks in KRC, using a chapter separator (`---` on its own line) before each new section. Check the actual pickup and preserve its NOTE time. The chapter ID travels with each structured lyric source so a template can restart its row order or add a count-in.

Save the editable project. Export one verified revision to a new directory with the [handoff writer](scripts/export_template_input.py). From the source checkout:

```sh
uv run namioto-ctl --instance ID project.export_source | uv run python skills/namioto-nicokara/scripts/export_template_input.py song-input --offset-ms -100
```

The writer checks the mapping and saves the matched `lyrics.krc`, decoded `vocal.mid` and an importable `song_input.py`. It carries the snapshot’s MIDI lead-in convention, track roles and project colours. Choose the signed offset for the recording (typically −100 ms for sing-along delivery); leave project notes on the musical timeline. With an existing verified KRC/MIDI pair, copy the [input module](assets/song_input.py) beside it and set the same facts there. Ordinary MIDI uses `WAVETONE = False`.

Call `load_song()` with the project interpreter and check the BPM, chapter boundaries, text, ruby and sung intervals against the reviewed phrases. Deliver the project, KRC/MIDI pair and song input together. This completes a timing-only request. For an optional pitch lane, also supply the reviewed melody MIDI.

The external kara-templater recipe receives this song and chooses layout, count-in graphics and effects. The delivery stage renders its Scene and checks it against the final mix. [Template authoring guide](https://github.com/sitiyou/python-kara-templater/blob/main/skills/kara-templater/SKILL.md)
