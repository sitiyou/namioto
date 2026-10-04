# SPDX-License-Identifier: AGPL-3.0-only
"""Pattern weights for mapping costs, independent of the aligner and the raw evidence.

A merge whose first or last Sound is a sokuon takes the edge weight; otherwise three or more
members take the size weight, and two members may take the empirical weight. The first rule wins.
Empirical vowel connections stay inside a Janome word whose reading agrees with the lyric; an
explicit long-vowel mark does not need a word boundary. Ambiguous ruby-to-word correspondence
receives no word-based preference. Natural Sounds and their readings are never changed. Qt-free.
"""

from __future__ import annotations

import unicodedata
from functools import lru_cache
from threading import local

from janome.tokenizer import Tokenizer

from namioto.karaoke.sounds import Sound, SoundLine

MERGE_SIZE_SLOPE = 0.5
EMPIRICAL_MERGE_WEIGHT = 0.8
EDGE_SOKUON_MERGE_WEIGHT = 1.5

_tokenizers = local()


def merge_weights(line: SoundLine) -> tuple[tuple[float, ...], ...]:
    """One row per starting Sound, indexed by merge member count minus two."""
    pairs = _empirical_pairs(line)
    return tuple(
        tuple(_weight(line, start, count, pairs[start]) for count in range(2, len(line.sounds) - start + 1))
        for start in range(len(line.sounds))
    )


def _weight(line: SoundLine, start: int, count: int, empirical: bool) -> float:
    if line.sounds[start].reading in ("っ", "ッ") or line.sounds[start + count - 1].reading in ("っ", "ッ"):
        return EDGE_SOKUON_MERGE_WEIGHT
    if count >= 3:
        return 1.0 + MERGE_SIZE_SLOPE * (count - 2)
    if empirical:
        return EMPIRICAL_MERGE_WEIGHT
    return 1.0


@lru_cache(maxsize=256)
def _empirical_pairs(line: SoundLine) -> tuple[bool, ...]:
    pairs = [False] * len(line.sounds)
    candidates = []
    for index, (first, second) in enumerate(zip(line.sounds[:-1], line.sounds[1:], strict=True)):
        vowel = first.token[-1:]
        if vowel not in ("a", "i", "u", "e", "o"):
            continue
        if second.reading == "ー":
            pairs[index] = True
        elif _hiragana(second.reading) in ("あ", "い", "う", "え", "お") and (
            vowel == second.token or (vowel, second.token) in (("a", "i"), ("o", "u"), ("e", "i"))
        ):
            candidates.append(index)
    if candidates:
        words = _word_ids(line)
        for index in candidates:
            pairs[index] = words[index] >= 0 and words[index] == words[index + 1]
    return tuple(pairs)


def _hiragana(text: str) -> str:
    return "".join(
        chr(ord(char) - 0x60) if "ァ" <= char <= "ヶ" else char for char in unicodedata.normalize("NFKC", text)
    )


@lru_cache(maxsize=256)
def _words(text: str) -> tuple[tuple[int, int, str], ...]:
    if not hasattr(_tokenizers, "tokenizer"):
        _tokenizers.tokenizer = Tokenizer()
    offset = len(text) - len(text.lstrip())
    words = []
    for token in _tokenizers.tokenizer.tokenize(text):
        end = offset + len(token.surface)
        if text[offset:end] != token.surface:
            return ()
        words.append((offset, end, token.reading))
        offset = end
    return tuple(words) if offset == len(text.rstrip()) else ()


def _word_ids(line: SoundLine) -> list[int]:
    ids = [-1] * len(line.sounds)
    regions = _regions(line)
    for word, (low, high, reading) in enumerate(_words(line.text)):
        if reading == "*":
            continue
        touched = [(start, end, indices) for start, end, indices in regions if start < high and end > low]
        if not touched or any(start < low or end > high for start, end, _indices in touched):
            continue
        indices = [index for _start, _end, owned in touched for index in owned]
        if _hiragana("".join(line.sounds[index].reading for index in indices)) != _hiragana(reading):
            continue
        for index in indices:
            ids[index] = word
    return ids


def _regions(line: SoundLine) -> list[tuple[int, int, tuple[int, ...]]]:
    """Whole readable source ranges; a ruby crossing word boundaries remains indivisible."""
    by_container: dict[int, list[Sound]] = {}
    for sound in line.sounds:
        by_container.setdefault(sound.container, []).append(sound)
    regions = []
    offset = 0
    for index, container in enumerate(line.containers):
        sounds = by_container.get(index, ())
        if container.key[0] == "top":
            source = "".join(container.atoms)
            regions.extend(
                (offset + sound.first_atom, offset + sound.last_atom + 1, (sound.index,)) for sound in sounds
            )
        else:
            if not sounds:
                return []
            source = sounds[0].base
            regions.append((offset, offset + len(source), tuple(sound.index for sound in sounds)))
        if line.text[offset : offset + len(source)] != source:
            return []
        offset += len(source)
    return regions if offset == len(line.text) else []
