# SPDX-License-Identifier: AGPL-3.0-only
"""Checks for the `.krc` writer: what survives a round trip and what it refuses to write."""

from __future__ import annotations

import pytest

from namioto.karaoke import Ruby, Unit, Word, dumps, parse
from namioto.karaoke.sounds import note_groups, sound_lines

SAMPLE = """@2{春[はる]の(風)[かぜ]が吹[ふ]く}
hello.3 world
(No).1 (Ring!).1

---

(幾千)[いくせん].1の夜[よる]
"""


def test_a_parsed_file_comes_back():
    lyrics = parse(SAMPLE)
    restored = parse(dumps(lyrics))
    original_sounds, restored_sounds = sound_lines(lyrics), sound_lines(restored)
    original_lines = [line for chapter in lyrics.chapters for line in chapter.lines]
    restored_lines = [line for chapter in restored.chapters for line in chapter.lines]
    assert [sound.token for row in original_sounds for sound in row.sounds] == [
        sound.token for row in restored_sounds for sound in row.sounds
    ]
    assert [note_groups(line, row) for line, row in zip(original_lines, original_sounds, strict=True)] == [
        note_groups(line, row) for line, row in zip(restored_lines, restored_sounds, strict=True)
    ]


def test_dumping_is_idempotent():
    once = dumps(parse(SAMPLE))
    assert dumps(parse(once)) == once


def test_a_track_and_a_chapter_separator_are_written():
    assert dumps(parse("あ\n\n---\n\n@2{い}")) == "あ\n\n---\n\n@2{い}"


def test_a_bare_latin_run_gains_no_dot():
    assert dumps(parse("hello")) == "hello"
    assert dumps(parse("hello.3")) == "hello.3"


def test_a_group_keeps_its_parentheses():
    assert dumps(parse("(F)(LO)(WER)")) == "FLOWER"
    assert dumps(parse("(Turn).1")) == "Turn"


def test_a_ruby_keeps_its_parts_and_dot():
    assert dumps(parse("何度目[なん,ど,め]")) == "何度目[なん,ど,め]"
    assert dumps(parse("(幾千)[いくせん].1")) == "(幾千)[いくせん].1"


def test_a_redundant_mora_override_is_removed_from_parsed_source():
    assert dumps(parse("(幾千)[いくせん].4")) == "(幾千)[いくせん]"
    assert dumps(parse("(おも).2")) == "おも"


def test_redundant_ruby_override_and_continuation_are_removed_together():
    assert dumps(parse("手[て.2]を.+")) == "手[て]を"


def test_an_override_and_a_ruby_show_up():
    lyrics = parse("(星)[ほし]")
    lyrics.chapters[0].lines[0].words[0].override_mora(1)
    assert dumps(lyrics) == "(星)[ほし].1"


def test_a_ruby_added_by_hand_shows_up():
    lyrics = parse("星")
    lyrics.chapters[0].lines[0].words[0].set_ruby(Ruby([[Unit(Word("ほ")), Unit(Word("し"))]]))
    assert dumps(lyrics) == "星[ほし]"


@pytest.mark.parametrize(
    "text", ["泣[な]い.+", "あい.+う.+", "あ(いう).+", "あ字[いう].+", "文字[い,う.+]", "あhello.+"]
)
def test_continuations_survive_writing_and_reparsing(text):
    assert dumps(parse(text)) == text
    assert dumps(parse(dumps(parse(text))), dotted=True) == text


def test_writing_refuses_overlapping_continuation_and_numeric_annotations():
    lyrics = parse("あい.+")
    lyrics.chapters[0].lines[0].units[-1].override = 2
    with pytest.raises(ValueError, match=r"both \.N and \.\+"):
        dumps(lyrics)


def test_a_word_that_cannot_be_written_is_refused():
    lyrics = parse("あ")
    lyrics.chapters[0].lines[0].words[0].base = Word("a#b")
    with pytest.raises(ValueError):
        dumps(lyrics)
