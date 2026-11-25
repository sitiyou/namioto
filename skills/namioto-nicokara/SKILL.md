---
name: namioto-nicokara
description: Time karaoke lyrics against a recording or revise a saved .nto in Namioto. Reuse matching MIDI and annotated lyrics when available.
---

# Time karaoke lyrics in Namioto

KRC (`.krc`) is Namioto's plain-text lyric format. It stores lyrics, readings, chapter breaks and how sounds map to notes. For example, `君[きみ]` adds the reading きみ to 君. [Write or convert lyrics to KRC](../../docs/lyrics.md#write-krc)

## Recording workflow

Start from the recording and lyrics, or reopen a saved `.nto` with its audio.

1. **Check the recording and prepare the project.** Confirm the audio clock and BPM, then check KRC wording, readings, phrase breaks and chapter entrances. Use [Qwen3-ASR](references/sung-words.md#check-lyric-readings) when a sung reading is unclear. [Project and lyrics](references/timing-from-audio.md#from-recording-to-saved-project)
2. **Build the GAME draft.** Keep raw and eighth-note-quantized results. Fit the grid offset and keep exact eighth-note lengths or multiples wherever the fit holds. Use the observed timing where the grid still conflicts with the phrasing after offset adjustment. [Measure and preview](references/timing-analysis.md#measure-and-preview) · [Grid fit check](references/timing-from-audio.md#check-the-eighth-note-grid)
3. **Align with MMS and review the draft phrase by phrase.** Compare raw and snapped GAME notes and check which sounds own them. Work through the [phrase review checks](references/timing-analysis.md#resolve-an-uncertain-phrase) for entrances, note shapes, releases and lyric ownership. [Overlapping voices](references/backing-vocals.md)
4. **Save and review the result as a whole.** Reopen the saved revision and check mapping exceptions, note patterns and phrase transitions. [Saved project checks](references/timing-from-audio.md#check-the-saved-project)

Save the checked `.nto` with its lyrics and notes. Record any harmony channels or delivery offset explicitly requested for the project. Keep the referenced recording available for further listening or editing.

If matching MIDI and KRC are already available, you can reuse their note times and lyric mapping. [Import existing timing](references/import-midi-krc.md)
