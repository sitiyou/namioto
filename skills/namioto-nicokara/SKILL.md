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

## Size the lyrics and choose line breaks

For horizontal lyrics on a 1920 × 1080 script canvas, audition the main vocal just above 100 script pixels (for example, 104) with the chosen face and outline. Scale the starting size by script height for other resolutions. Fonts at the same nominal size have different visible heights. Preview a normal verse and a dense chorus at delivery size, then choose a consistent body size that leaves room for ruby, cues and the picture.

Plan display lines from the source wording before alignment. Read the phrase aloud and mark boundaries where a clause, a breath or a musical answer completes a thought. Keep words, names, ruby groups and short English phrases together. In Japanese, keep a particle with its phrase and avoid starting a line with closing punctuation. For example, 「窓を開けたら｜海の匂いがした」 gives the listener two complete phrases. Treat the bar here as an editorial break mark. Enter the chosen break as a lyric line boundary.

Measure candidates with the actual font, ruby, outline and inline cue inside the track's usable margins. Split an overwide line at the strongest phrase boundary before reducing the song's type size. A line that fits can still deserve a break when densely packed readings or a fast second clause make it hard to scan before singing. Play both candidates through the row handoff. Keep a short pickup attached if giving it a row would leave the listener too little preview time. After reflowing an already timed passage, verify that the sung tokens, note ownership and highlight onsets survive the new line boundaries.

In an alternating layout, show at most one main-vocal line on the left and one on the right at any instant. Give each phrase a single row on its assigned side and alternate successive display lines. A side stays occupied from the first visible preview through the last fade-out frame. For a quick left–right–left exchange, check the frame where the third line appears. If the first line is still visible on the left, shorten its post-phrase hold or fade, or delay the third line’s preview while preserving enough time to read the pickup. Keep the sung timings intact. Apply this check even when the two lines on one side would fit without touching. An inline response shares its host row; a separate upper harmony track has its own placement.

For geometric preflight, compare each measured `LayoutLine.bounds`, translated by its final `Placement.position`, with the lyric region and canvas. Check intersections between distinct lyric lines whose display intervals overlap, including visible previews, tails and harmony. A useful warning identifies the lyric text, time, track and affected edge or neighboring line. Treat intersecting boxes as candidates for visual review; intentional layers on the same lyric share geometry. The built-in row allocator reports insufficient timing capacity. Animated scale, glow and moving decorations also need frame checks at their largest extent. See [track measurement and allocation](https://github.com/sitiyou/python-kara-templater/blob/main/docs/guides/tracks.md#allocation-is-the-layout-extension-point).

## Time lyrics against the voice

1. Prepare the lyrics and their sung readings in KRC. Check how Japanese kana, kanji readings, numbers and foreign words expand into natural sounds. Keep the source lyrics available for comparison.
2. For model-assisted alignment of a new project, open the isolated vocal with `audio.open`, wait for `state.get` to report loaded audio, then submit the KRC with `lyrics.set`. On a supported language and channel, start `analysis.align`. Poll `analysis.alignment job=ID` on the same instance. Its time pairs, scores and onset bounds follow the natural sounds on that channel. Save the evidence before starting another alignment.
3. Request melody evidence with `analysis.transcribe` and poll `analysis.transcription job=ID`. GAME returns onset and offset in seconds and fractional MIDI pitch. Compare uncertain boundaries against the note attacks, rests and neighboring phrases. For Japanese vocals, disagreements between MMS and Yohane are useful places to inspect. Use `spectrum.frames start=SECONDS count=N` for local pitch-band evidence. Instrumental leakage can trigger spurious notes.
4. Apply edits on the intended channel. For a newly built complete note list, `document.replace_notes` accepts pitch, start and duration in seconds. It replaces the whole list, so preserve other channels when editing an existing song. When one sound spans several notes, encode its NOTE count in the KRC. Round neighboring endpoints on a shared time grid before deriving durations.
5. Save the project with `project.save`. Wait for `state.get` to clear `subtitle.pending`. Use `project.export_source` to check the resulting KRC and MIDI. Reopen the saved project in another session and compare the exported source, particularly short notes at a non-default tempo.

## Backing vocals and sung responses

By default, time the supplied lyrics and skip searching the recording for unprinted backing vocals. Discovery takes time and short calls are easy to misidentify. When the supplied lyrics already include responses or mark another voice, identify the singer and timing for those passages as part of normal alignment. Search for additional parts only on request, starting at the user's timecode when given.

Compare the phrase in the original mix with a backing-vocal stem if available. For stereo audio, the difference between left and right channels can bring out a panned response. Include the lead-in, then isolate the suspected answer and compare its attack and ending with the lead. A second voice may repeat exactly the printed word, enter on its final syllable, or continue underneath the next line. A loud side signal alone can also come from reverb or instruments. If using speech recognition, transcribe without supplying the suspected words and try both the phrase and the short excerpt. Record the time, overlap and likely wording separately. Keep uncertain syllables as review notes until resolved.

Choose the layout by how the singer will read the phrase. In either layout, give the backing part its own timed notes, lyric channel and distinct highlight colour. Preserve each voice's performed onset and release, including overlap.

- **Inline parentheses** suit a short echo or call that reads naturally next to the word it answers and fits comfortably on the row. A backing voice can enter while the lead is still holding that word. Put the response in its own inline channel wrapper on the same display line. The default template can sweep both text spans simultaneously, each on its own clock. ASCII parentheses are KRC grouping syntax. For Japanese lyrics, use visible punctuation such as `（ ）` and verify the brackets consume no sung notes.
- **A separate upper track** suits an independently phrased countermelody, a response that continues across a lead line change, or a passage where inserting the response would crowd the row or interrupt the lead sentence. Put the backing lyrics on their own display line and assign their channel to the upper track with `harmony_tracks`.

Preview the handoff at singing speed. During an overlap, check a frame where both voices are active and verify each highlight advances on its own words. For an upper track, also check clearance around ruby and count-ins. See [lyric channel placement](../../docs/lyrics.md#assign-lyric-channels).

## Optional tempo and grid references

When a phrase is hard to interpret, the accompaniment or mix can provide a rhythmic reference. Start `analysis.detect_bpm algorithm=wavetone` and poll `analysis.bpm_result job=ID`. Compare plausible candidates, including half or double tempo, against several passages. Set a project tempo with `transport.set_bpm` only when an editor grid is wanted.

For a completed GAME run, `analysis.transcription` returns the measured attacks. An optional `analysis.transcription_grid job=ID cells=4 estimate_phase=true` previews the same notes on a sixteenth-note grid at the editor's current BPM. It also estimates the subdivision offset. Compare a suspicious fragment with the vocal, lyric reading and surrounding notes. Preserve audible early or late entries, repeated attacks and sustained pitches. Listen to the accompaniment to identify a bar downbeat if needed. Applying a grid offset or replacing notes is a separate editing decision.

## Mark vocal entrances with count-ins

Listen or inspect the vocal timeline for the first entrance and re-entries after instrumental breaks. Mark the pickup itself, even if it precedes the downbeat. With Namioto’s default template, place a KRC chapter separator (`---` on its own line) before each selected re-entry. Ordinary line and stanza breaks serve lyric phrasing. The default template attaches a lead cue to the first main-vocal line of each chapter when there is enough pre-roll.

Choose dots or bars and a step duration the singer can follow at the song tempo. Check the whole count fits in the available rest; a short pickup may need a faster count or no additional cue. Reserve horizontal space for inline symbols or vertical clearance for separate symbols, including ruby and any other visible row. Use a contrasting fill and outline. A custom template must render the lead marker on the corresponding track.

On the delivery render, inspect the start, middle and end of each planned count-in plus the first sung frame. Verify the symbols visibly progress toward the vocal entrance, clear the lyric on time in each requested edition. Preserve the singer’s note times when adding chapter or line breaks.

## Choose a template and render

Choose the palette from the supplied visuals. Sample a recurring accent from representative MV frames, cover art or the background illustration for the lead highlight. For backing vocals, choose a second accent that stays visibly distinct beside the lead. Adjust lightness, saturation and outline against the actual lyric region. Preview unsung text, the moving highlight, ruby and cues over both bright and dark shots. Include a frame with both voices active when present. With audio alone, use the project palette as the starting point.

Start with the project's chosen font. Check the installed face and weight using actual lyrics, including the longest line and the smallest ruby. For a new Python template, follow [Namioto template registration](https://github.com/sitiyou/namioto/blob/main/docs/karaoke-templates.md). Consult kara-templater's [layout tutorial](https://github.com/sitiyou/python-kara-templater/blob/main/docs/tutorials/karaoke-design.md), [effects guide](https://github.com/sitiyou/python-kara-templater/blob/main/docs/guides/karaoke-effects.md), [font and ruby guide](https://github.com/sitiyou/python-kara-templater/blob/main/docs/guides/fonts-and-preview.md) and [output guide](https://github.com/sitiyou/python-kara-templater/blob/main/docs/guides/choosing-output.md). Load only templates the user trusts.

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

## Optional extra on request: MIDI pitch visualization

Offer a scrolling pitch lane only when the user requests a visualizer edition. Ordinary karaoke exports are complete with the lyric hook or ASS subtitle.

Build a separate display melody phrase by phrase. Use the lyric text and readings to locate sung phrases, the vocal recording to identify attacks and sustained pitches, and raw transcription as a set of candidate notes. Several reading units can share a held vowel; one sung syllable can also carry several pitches. Subtitle highlight boundaries and minimum spacing inserted by a lyric aligner can produce tiny artificial notes when copied into a pitch lane.

For adjacent notes of the same pitch, check the **second onset** for a fresh consonant or vowel attack, a break in voicing, or a matching articulation in a repeated phrase. When a transcription note spans an unsupported lyric boundary, draw the continuous note from its original attack. Missing pitch estimates leave the boundary uncertain. Compare the recording and surrounding notes before changing it.

Use an estimated tempo and subdivision phase to flag unusually close attacks for inspection. Keep supported pickups and expressive timing at their performed positions. Compare each phrase's displayed notes with its sung attacks and stable pitch changes; a song-wide note count or grid-error statistic alone cannot establish a good transcription. For a reported visual defect, locate the exact time and verify both the saved MIDI and a delivered video frame there. Also check a rapid phrase, a sustained note and a repeated refrain.

For a requested pitch lane, use the [bundled visualizer](scripts/midi_visualizer.py) from a configured Namioto source checkout. Supply a reviewed lead-melody MIDI on the lyric timeline and the exported single-pass Namioto lyric hook. The script writes a separate combined hook for this edition. Run it directly on Windows or Unix:

```sh
uv run python skills/namioto-nicokara/scripts/midi_visualizer.py "song.mid" "lyrics.hook" --fps 60 --out "lyrics-with-pitch.hook"
```

Match the hook and render frame rates. Add `--wavetone` if the MIDI export includes the four-beat WaveTone lead-in. Preview the combined hook using the same renderer and check that note attacks reach the marker at the corresponding sung syllables. This helper targets the Namioto single-pass hook format.
