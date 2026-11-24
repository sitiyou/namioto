# MIDI pitch visualization

Prepare a display melody when the requested edition includes a scrolling pitch lane.

Build a separate display melody phrase by phrase. Use the lyric text and readings to locate sung phrases, the vocal recording to identify attacks and sustained pitches, and raw transcription as a set of candidate notes. Several reading units can share a held vowel; one sung syllable can also carry several pitches. Subtitle highlight boundaries and minimum spacing inserted by a lyric aligner can produce tiny artificial notes when copied into a pitch lane.

For adjacent notes of the same pitch, check the **second onset** for a fresh consonant or vowel attack, a break in voicing, or a matching articulation in a repeated phrase. When a transcription note spans an unsupported lyric boundary, draw the continuous note from its original attack. Missing pitch estimates leave the boundary uncertain. Compare the recording and surrounding notes before changing it.

Use an estimated tempo and subdivision phase to flag unusually close attacks for inspection. Keep supported pickups and expressive timing at their performed positions. Compare each phrase's displayed notes with its vocal attacks and stable pitch changes. Check a rapid phrase, a sustained note and a repeated refrain.

Identify the reviewed lead-melody channel in the saved project. If a separate display melody is needed, deliver its MIDI on the original-song clock and record whether it uses a WaveTone lead-in. Hand off the melody choice with the saved project for rendering.
