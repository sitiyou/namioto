# Time lyrics against a recording

Use the confirmed recording as the clock. For GAME, use a vocal stem aligned to that recording. Confirm the match at the beginning, middle and end.

## From recording to saved project

1. **Establish tempo and open the project.** For a new project, estimate BPM on the original mix or matching accompaniment and check it against the beat across the recording. Open the aligned vocal in Namioto, save the `.nto`, and set the confirmed BPM. With only the mix available, use it for manual timing. When revising an existing `.nto`, retain its referenced audio and start from its verified tempo and grid offset. If it references the mix and you need new GAME evidence, analyze the aligned vocal in a separate session and use those times in the project being revised.
2. **Prepare the project lyrics.** Write or convert the lyrics to KRC, or import an existing KRC file. Check the reading against the performance. Read the lyrics aloud and divide visible lines at clause, breath or musical-response boundaries. Keep words, names, ruby groups and short English phrases together. In Japanese, keep particles with their phrases and closing punctuation with the preceding text. Mark a new chapter after an instrumental break with `---` on its own line and preserve the performed pickup. [KRC syntax and conversion](../../../docs/lyrics.md#write-krc)
3. **Transcribe with GAME and fit the eighth-note grid.** Keep the raw GAME result, then estimate the grid offset and compare the snapped notes against the recording. Use the [grid fit check](#check-the-eighth-note-grid) to decide which notes to keep and where a local correction is needed. If GAME is unavailable, draft notes by ear and with the spectrum, then apply the same grid fit check. [GAME and grid controls](timing-analysis.md#measure-and-preview)
4. **Review the notes and the mapping together.** Use the [phrase review](timing-analysis.md#resolve-an-uncertain-phrase). In the lyrics strip, check which notes each sound consumes through to the first sound of the next phrase. If the selected channel needs a new mapping and the project has the aligned vocal loaded, use **Rebuild mapping** and check the resulting mapping. With overlapping voices, check each channel's onset and release independently. [Mapping controls](../../../docs/lyrics.md#map-lyrics-to-notes) · [Backing vocals](backing-vocals.md)
5. **Save and verify the `.nto`.** Follow the [saved project checks](#check-the-saved-project).

## Check the eighth-note grid

Start from the estimated offset and compare attacks and releases in phrases near the beginning, middle and end. Check the grid against the beat in the mix or accompaniment. GAME's acoustic attacks can lean consistently ahead of or behind that beat. Adjust the offset when the snapped notes are consistently early or late. If the error grows through the song, recheck BPM before trying another offset. Check a quick phrase, a held note and a repeated refrain. A good fit preserves the phrasing, including distinct attacks and breathing gaps.

**Keep exact eighth-note lengths as the normal result.** For a passage that fits, keep both note boundaries on the confirmed grid. Each note lasts one eighth note or a whole multiple of it. At 120 BPM, one grid cell is 250 ms.

If the passage still conflicts with the performed phrasing after adjusting the offset, use the grid as a reference there. Estimate the affected attacks and releases from the available vocal evidence and keep those durations. Leave the passages that fit on the confirmed grid.

## Edit through IPC

Confirm the target project and instance with `state.get`. For a new agent-owned session, use `audio.open` for the recording and supply a path to `project.save`. Use `project.open` for an existing `.nto`. `lyrics.set` takes reviewed KRC. For local changes, read the target channel with `analysis.phrase`, preview with `document.apply_phrase`, then submit with preview disabled. [Session setup](../../../docs/cli.md#remote-control) · [Phrase example](../../../docs/cli.md#review-and-edit-one-phrase)

To initialize an empty roll from a checked GAME grid, set the BPM and grid offset, then send the reviewed note list with `document.replace_notes`. Each note takes `start`, `duration`, and an integer MIDI `pitch`, with optional `channel`. Convert the grid's end timestamp to a duration and keep all times in seconds. Put the reviewed lyrics in `lyrics.set` and use **Rebuild mapping** in the editor for each vocal channel, or supply reviewed KRC with its NOTE mapping already authored. `analysis.align` provides the onset evidence for an automated pass. Save when the mapping agrees with the notes, then reopen and verify the project.

## Check the saved project

Save with `Ctrl+S` or `project.save` and let the lyric mapping finish. Over IPC, check `subtitle.pending` in `state.get`. Reopen the saved `.nto` and verify the BPM, grid offset, KRC line breaks, readings and chapter entrances.

- **KRC mapping.** Most lyrics should use the default note-slot counts. Compare NOTE coverage and requested slots on each vocal channel. `document.notes` includes the full note stream, and `analysis.phrase` gives the slot groups and owned note IDs for each line. Check unmatched notes at chapter gaps and the end, and review clusters of `.N`, zero slots and shared groups using the [phrase review checks](timing-analysis.md#resolve-an-uncertain-phrase).
- **Timing and pitch.** In the piano roll, look for brief same-pitch restarts, short notes followed by long gaps inside a phrase, isolated octave jumps and same-voice overlaps. Check held vowels, pickups and breathing gaps. Compare repeated phrases for consistent note shapes and lyric ownership, while allowing performed variations. Use the agreed grid for sections that fit.
- **Phrase transitions.** Check the first entrance, places where the song resumes after a break, revised line breaks and overlapping voices. With a preview available, watch the timing at normal speed and check whether rapid wipes, stalls or late line changes reflect the phrase. Listen when audio can be reviewed.

Treat these checks as a combined judgment. GAME, lyric alignment and the spectrum can each leave details ambiguous. Record timing that remains uncertain and matters to the result.

Record any remaining uncertain timing, chosen harmony channels or explicit delivery offset with the project. For a requested pitch lane, identify the reviewed melody channel or prepare a [separate melody](pitch-visualization.md).
