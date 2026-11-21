# MIDI pitch visualization

Offer a scrolling pitch lane only when the user requests a visualizer edition. Ordinary karaoke exports are complete with the lyric hook or ASS subtitle.

Build a separate display melody phrase by phrase. Use the lyric text and readings to locate sung phrases, the vocal recording to identify attacks and sustained pitches, and raw transcription as a set of candidate notes. Several reading units can share a held vowel; one sung syllable can also carry several pitches. Subtitle highlight boundaries and minimum spacing inserted by a lyric aligner can produce tiny artificial notes when copied into a pitch lane.

For adjacent notes of the same pitch, check the **second onset** for a fresh consonant or vowel attack, a break in voicing, or a matching articulation in a repeated phrase. When a transcription note spans an unsupported lyric boundary, draw the continuous note from its original attack. Missing pitch estimates leave the boundary uncertain. Compare the recording and surrounding notes before changing it.

Use an estimated tempo and subdivision phase to flag unusually close attacks for inspection. Keep supported pickups and expressive timing at their performed positions. Compare each phrase's displayed notes with its sung attacks and stable pitch changes; a song-wide note count or grid-error statistic alone cannot establish a good transcription. For a reported visual defect, locate the exact time and verify both the saved MIDI and a delivered video frame there. Also check a rapid phrase, a sustained note and a repeated refrain.

For a requested pitch lane, use the [bundled visualizer](../scripts/midi_visualizer.py) from a configured Namioto source checkout. Supply a reviewed lead-melody MIDI on the lyric timeline and the exported single-pass Namioto lyric hook. The script writes a separate combined hook for this edition. Run it directly on Windows or Unix:

```sh
uv run python skills/namioto-nicokara/scripts/midi_visualizer.py "song.mid" "lyrics.hook" --fps 60 --out "lyrics-with-pitch.hook"
```

Match the hook and render frame rates. Add `--wavetone` if the MIDI export includes the four-beat WaveTone lead-in. Preview the combined hook using the same renderer and check that note attacks reach the marker at the corresponding sung syllables. This helper targets the Namioto single-pass hook format.
