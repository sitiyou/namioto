# Check sung words with Qwen3-ASR

Use Qwen3-ASR on a specific phrase when a lyric reading or the words of a backing response are unclear. Short clips give the model little context, so it can turn an unfamiliar sound into a plausible but incorrect word. Ask what the singer could have sung to produce that transcript. Compare the candidate sounds with the full phrase, printed lyrics and repeated passages before editing the KRC.

## Transcribe a passage

Reuse a configured Qwen3-ASR runtime, or follow the [official setup for Qwen3-ASR-1.7B](https://huggingface.co/Qwen/Qwen3-ASR-1.7B#quickstart). Cut a clip around the phrase from the aligned vocal and note its position on the recording. Start with the full phrase and try a shorter clip if necessary. Set the language to Japanese for Japanese lyrics. Pass the audio without supplying the suspected words or reading.

For example, the official `qwen-asr` package can run this clip on a CUDA device:

```python
import torch
from qwen_asr import Qwen3ASRModel

model = Qwen3ASRModel.from_pretrained(
    "Qwen/Qwen3-ASR-1.7B", dtype=torch.bfloat16, device_map="cuda:0"
)
print(model.transcribe(audio="phrase.wav", language="Japanese")[0].text)
```

## Check lyric readings

1. Review the reading when the lyrics give an unusual reading or the alignment repeatedly skips or shifts around one word. Forced alignment uses the sounds derived from KRC, so an incorrect reading can also displace neighboring sounds.
2. Compare the transcript with the printed ruby if available, the phrase context, and another performance of the line if the song repeats it. The transcript can suggest a different pronunciation through kana or a different word. When it returns the same kanji, the spelling alone leaves the pronunciation unresolved.
3. Preserve the printed lyric and change its KRC reading to the one supported by the performance. For example, if the lyric displays 宇宙 and the singer sings そら as a meaning-based reading (義訓), write `宇宙[そら]`. Re-run lyric alignment for the affected channel and check the edited phrase and its neighbors. If the evidence is inconclusive, record the candidate reading for review.

When you have two candidate readings, use [clip analysis](../../../docs/cli.md#inspect-an-audio-clip) to compare them with MMS or Yohane on the same vocal passage. Keep their surrounding words identical. Use the score and alignment to judge which reading better explains the recording before changing KRC.

## Identify a backing response

Compare the transcript of the full phrase with the transcript of the short response. Use a backing-vocal stem if available, and cross-check against the original phrase or the stereo difference. For example, if a short clip returns `Go!` just after the lead sings 「グー」, test whether the response echoes 「グー！」. Compare the vowel, duration and wording with the lead before choosing a reading. Use the audio evidence to determine who sang the response and when. [Backing vocal checks](backing-vocals.md)
