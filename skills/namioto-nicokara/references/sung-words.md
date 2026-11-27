# Check sung words with RomajiASR

When native audio input is available, listen directly to a short clip of the isolated vocal (slowed to `0.65×–0.7×` for fast passages, or `L - R` side-isolated for panned calls) to check Japanese lyric readings, elisions, puns and non-lexical calls first. Reserve RomajiASR for environments without audio input or syllables that remain indistinct after listening.

Use RomajiASR when a Japanese lyric reading or the words of a backing response are unclear. It proposes sung sounds as romanized tokens, including morae such as `shi` and the geminate marker `cl` for っ. Compare the proposed sounds with the full phrase, printed lyrics and repeated passages before editing the KRC.

## Transcribe a passage

Select a window around the full phrase on the isolated vocal first. Shorten the window to inspect a specific response if needed. Use the token times to locate the sounds for playback. On an already cut clip, give `--audio-origin` to retain the recording clock.

```sh
namioto-cli clip vocal.wav --model romajiasr --start 22.3 --end 25.5
```

The model downloads on first use and uses Namioto's ONNX Runtime. See [CLI output and IPC usage](../../../docs/cli.md#recognize-japanese-sung-sounds).

## Check lyric readings

1. Review the reading when the lyrics give an unusual reading or the alignment repeatedly skips or shifts around one word. Forced alignment uses the sounds derived from KRC, so an incorrect reading can also displace neighboring sounds.
2. Compare the proposed sounds with the printed ruby if available, the phrase context and another performance of the line if the song repeats it. Check likely omissions and substitutions by listening across the whole phrase.
3. Preserve the printed lyric and change its KRC reading to the one supported by the performance. For example, if the lyric displays 宇宙 and the singer sings そら as a meaning-based reading (義訓), write `宇宙[そら]`. Re-run lyric alignment for the affected channel and check the edited phrase and its neighbors. If the evidence is inconclusive, record the candidate reading for review.

When you have two candidate readings, use [clip analysis](../../../docs/cli.md#inspect-an-audio-clip) to compare them with MMS or Yohane on the same vocal passage. Keep their surrounding words identical. Use the score and alignment to judge which reading better explains the recording before changing KRC.

## Identify a backing response

Compare the recognized sounds from the full phrase and from the short response. Use a backing-vocal stem if available and cross-check against the original phrase or the stereo difference. If the model suggests `go` for a response to 「グー」, compare its vowel and duration with the lead to check for an echo. Listen for which voice sang the response and when. [Backing vocal checks](backing-vocals.md)
