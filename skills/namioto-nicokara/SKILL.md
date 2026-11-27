---
name: namioto-nicokara
description: Time karaoke lyrics against a recording or revise a saved .nto in Namioto. Reuse matching MIDI and annotated lyrics when available.
---

# Time karaoke lyrics in Namioto

KRC (`.krc`) is Namioto's plain-text lyric format. It stores lyrics, readings, chapter breaks and how sounds map to notes. For example, `君[きみ]` adds the reading きみ to 君. [Write or convert lyrics to KRC](../../docs/lyrics.md#write-krc)

## Recording workflow

Start from the recording and lyrics, or reopen a saved `.nto` with its audio.

**Keep NOTE starts and ends on the confirmed eighth-note grid by default.** Use a verified score for the matching recording when available. Otherwise, establish the grid from the accompaniment and repeated phrases. Before using a finer or off-grid boundary, identify the rhythmic event or rest that the eighth-note version would lose; for sustained rapid-fire passages (such as sixteenth-note runs where eighth-note snapping collapses consecutive syllables), use the sixteenth-note grid for those runs while keeping regular passages on the eighth-note grid. Keep the eighth-note boundary when both versions preserve the same phrasing. Model timestamps and measurements of an attack or release are estimates of the performance. Map the sounds to the resulting notes. Share a NOTE when its equal divisions fit the sung entries. Reserve `.0` for passing sounds that need no separate timing. Give a written sound an [explicit interval in a pause inside a continuous phrase](references/timing-analysis.md#place-a-sound-in-an-internal-pause).

**With native audio input**, pair direct listening with deterministic timing anchors instead of text-only workarounds: listen to the isolated vocal (and mix) in ~30–40 s chunks up front to verify lyric wording, readings, elisions, phrasing density and any backing parts directly — skip ASR (`RomajiASR`) and spectrogram PNG tracing when listening already resolves what was sung. Still use accompaniment beat phase, GAME pitches, MMS/Yohane onset consensus and vocal RMS for millisecond-accurate boundaries, targeting short slowed-down (`0.65×–0.7×`) or stereo `L-R` side clips only where models disagree or voices overlap.

1. **Check the recording and prepare the project.** Confirm the audio clock, BPM and accompaniment beat phase, then check KRC wording, readings, phrase breaks and chapter entrances. Listen directly when audio input is available, or use [RomajiASR](references/sung-words.md#check-lyric-readings) to suggest candidates when a Japanese sung reading is unclear. [Project and lyrics](references/timing-from-audio.md#from-recording-to-saved-project)
2. **Build the GAME draft.** Keep raw and grid-quantized results (`cells=2` eighth-note baseline, or `cells=4` where sustained sixteenth-note runs dominate). Fit the grid offset against the accompaniment beat and use the quantized result as the editing baseline. [Measure and preview](references/timing-analysis.md#measure-and-preview) · [Grid fit check](references/timing-from-audio.md#check-the-eighth-note-grid)
3. **Align with MMS (and Yohane) and review the draft phrase by phrase.** Compare raw and snapped GAME notes alongside alignment consensus and the local signal summary. Use model disagreements, flagged boundaries and overlapping handoffs to focus review, then check which sounds own the chosen notes. [Phrase review checks](references/timing-analysis.md#resolve-an-uncertain-phrase) · [Overlapping voices](references/backing-vocals.md)
4. **Save and review the result as a whole.** Reopen the saved revision and check boundaries outside the eighth-note grid, along with mapping and phrase transitions. [Saved project checks](references/timing-from-audio.md#check-the-saved-project)

Save the checked `.nto` with its lyrics and notes. Record any harmony channels or delivery offset explicitly requested for the project. Keep the referenced recording available for further listening or editing.

If matching MIDI and KRC are already available, you can reuse their note times and lyric mapping. [Import existing timing](references/import-midi-krc.md)
