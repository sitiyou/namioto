# SPDX-License-Identifier: AGPL-3.0-only
"""Optional passes over a parsed `.krc`, each one `Lyrics -> Lyrics` and free of side effects.

A pass returns a new model and leaves its argument alone, so the caller decides whether to use it.
"""

from __future__ import annotations

from namioto.karaoke.model import Chapter, Line, Lyrics, Ruby, Word, validate


def merge_words(lyrics: Lyrics) -> Lyrics:
    """Fold each run of kanji, or of Latin letters, into the word that carries the ruby."""
    chapters = []
    for chapter in lyrics.chapters:
        lines = [Line([_merge_ruby(word) for word in _fold(line.words)], track=line.track) for line in chapter.lines]
        chapters.append(Chapter(lines))
    merged = Lyrics(chapters)
    validate(merged)
    return merged


def _merge_ruby(word: Word) -> Word:
    if word.ruby is None:
        return word
    return Word(word.text, Ruby([_fold(part) for part in word.ruby.parts]), word.override, word.grouped)


def _fold(words: list[Word]) -> list[Word]:
    merged: list[Word] = []
    for word in _fold_kanji(words):
        if (
            merged
            and not word.grouped
            and not merged[-1].grouped
            and word.is_latin()
            and merged[-1].is_latin()
            and merged[-1].ruby is None
        ):
            previous = merged.pop()
            override = word.override if word.override is not None else previous.override
            word = Word(previous.text + word.text, word.ruby, override, word.grouped)
        merged.append(word)
    return merged


def _fold_kanji(words: list[Word]) -> list[Word]:
    pending = list(words)
    folded: list[Word] = []
    while pending:
        word = pending.pop()
        if word.ruby is not None and word.is_kanji():
            text = word.text
            while pending and pending[-1].is_kanji() and pending[-1].ruby is None:
                text = pending.pop().text + text
            if text != word.text:
                word = Word(text, word.ruby, word.override, word.grouped)
        folded.insert(0, word)
    return folded


def flatten_ruby(lyrics: Lyrics) -> Lyrics:
    """Give every ruby word one mora, by writing one word per mora of its ruby."""
    chapters = [Chapter([_flatten_line(line) for line in chapter.lines]) for chapter in lyrics.chapters]
    return Lyrics(chapters)


def _flatten_line(line: Line) -> Line:
    words: list[Word] = []
    for word in line.words:
        if word.ruby is None or not word.is_ruby_mora:
            words.append(word)
            continue

        if word.mora == 1:
            part = word.ruby.parts[0]
            if len(part) == 1:
                words.append(word)
                continue

        for part_idx, part in enumerate(word.ruby.parts):
            part_text = word.text if len(word.ruby.parts) == 1 else word.text[part_idx]
            for i, inner in enumerate(part):
                text = ("#" * bool(part_idx != 0) + part_text) if i == 0 else "#"
                flat = Word(text)
                flat.set_ruby(Ruby([[inner]]))
                words.append(flat)
    return Line(words, track=line.track)
