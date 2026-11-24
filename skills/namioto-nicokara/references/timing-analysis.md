# GAME and alignment controls

## Measure and preview

1. Measure BPM on the accompaniment or mix with `analysis.detect_bpm algorithm=wavetone`. Poll `analysis.bpm_result job=ID` and compare beats near the beginning, middle and end. Check half or double tempo and any growing displacement before moving notes.
2. Run GAME on the aligned vocal at the confirmed tempo. The editor keeps the raw inference result when it inserts quantized notes. Through IPC, set the tempo with `transport.set_bpm`, run `analysis.transcribe`, and get the raw attacks, releases and pitches with `analysis.transcription job=ID`.
3. In the GAME dialog, use **Quantize → 1/8** and **Estimate phase**. To try a different offset, adjust **Grid offset (ms)** in the editor and rerun with **Estimate phase** cleared. GAME can reuse the same raw result. Through IPC, preview with `analysis.transcription_grid job=ID cells=2 estimate_phase=true`, use `transport.set_grid_offset ms=VALUE` to set the offset, and call `analysis.transcription_grid job=ID cells=2` to preview it. Setting the offset moves the grid. The IPC preview returns proposed note times for a separate edit. [Grid fit check](timing-from-audio.md#check-the-eighth-note-grid)

## Resolve an uncertain phrase

For Japanese lyrics, compare MMS and Yohane on the same reviewed KRC, audio and channel when both models are available. Set the readings with `lyrics.set`, run `analysis.align` with `model=mms` and then `model=yohane` for that channel, and poll each job with `analysis.alignment job=ID`. Use `analysis.phrase line=INDEX channel=CHANNEL` for the current notes, mapping, both GAME lists and the aligners' times, scores and onset bounds. Check `missing` and request `spectrum=true` or `rms=true` for disputed passages. Its quantized GAME list uses the estimated phase. If you chose a different offset, use the preview from `analysis.transcription_grid` at that offset.

| Evidence | What to check |
| --- | --- |
| Fitted GAME grid | Check musical placement and count the cells across held notes. Compare the accompaniment rhythm and repeated phrases. |
| Raw GAME | Look for attacks or rests lost in quantization. A continuous vowel or vibrato with one clear attack usually belongs to one note. A distinct reattack can justify another note at the same pitch. |
| Spectrum and audio | Review the phrase with its neighbors. Listen to the vocal and mix when available. Follow groups of harmonics for pitch steps and releases, and use short-window RMS for rises and gaps. Consider breaths, vibrato and instrumental bleed when GAME produces short fragments. [Audio inspection script](../scripts/inspect_audio.py) |
| Lyric alignment | Use the reading sequence and model onsets to assign sounds to notes. If models disagree by a syllable or a word, find the last clear sound before and the next clear sound after the disagreement, then resolve the intervening attacks in order. If the sung sounds seem different from the KRC reading, [check the reading](sung-words.md#check-lyric-readings) before editing notes. Determine NOTE releases from the vocal, harmonics and GAME note-offs. A CTC token end marks the end of the model's token, so it is unsuitable for deciding a NOTE release. |

Start with one NOTE per sung sound. Merge a false split of one held vowel and keep distinct melodic steps as separate NOTE slots. Use shared groups when sounds share a note and zero slots for displayed text without a note. Keep punctuation and symbols such as ☆, ♥ and ♪, then check that the next sound still owns its intended note. For a local revision, check the next sound in the [phrase preview](../../../docs/cli.md#review-and-edit-one-phrase) before committing. For a full import, `document.replace_notes` takes the entire list in seconds, including all channels.

Anchor ambiguous boundaries between repeated or similar sounds to the recording. Locate the last sustained sound before the gap and the next clear attack using the vocal, harmonic continuity, and short-window energy. Use the words on either side to assign the sounds across that boundary. Several written sounds can share the sustained note before a pause, and a single sound after the pause can span several pitch notes. Check that the later sounds retain their intended notes.

For a reported wipe jump followed by a stall, locate the source notes using the original-song time and known delivery offset. Check attack and duration separately. Inspect very short notes followed by long gaps inside a voiced phrase. A short consonant closure can be part of a continuous gesture. Preserve a genuine release, rest and re-entry. Confirm the release with harmonic decay and raw GAME note-offs, then verify the notes in the saved `.nto`.

For a disputed pitch or attack, run the [independent clip analysis](../../../docs/cli.md#inspect-an-audio-clip) with GAME on just that passage. A filtered or separated clip can be analyzed the same way. Keep the clip on the recording clock when comparing with the project notes.
