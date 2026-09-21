# SPDX-License-Identifier: AGPL-3.0-only

from namioto import utils


def test_kana_tokens_splits_kana_into_one_hepburn_token_per_mora():
    assert utils.kana_tokens("きょう") == ["kyo", "u"]
    assert utils.kana_tokens("しんぶん") == ["shi", "n", "bu", "n"]
    assert utils.kana_tokens("ふぁんた") == ["fa", "n", "ta"]


def test_kana_tokens_doubles_a_sokuon_onto_the_next_mora():
    assert utils.kana_tokens("がっこう") == ["ga", "kko", "u"]
    assert utils.kana_tokens("ちょっと") == ["cho", "tto"]


def test_kana_tokens_repeats_the_vowel_after_the_long_vowel_mark():
    assert utils.kana_tokens("コーヒー") == ["koo", "hii"]
    assert utils.kana_tokens("ふぁんたじー") == ["fa", "n", "ta", "jii"]


def test_kana_tokens_folds_katakana_onto_hiragana():
    assert utils.kana_tokens("キョウ") == utils.kana_tokens("きょう")


def test_kana_tokens_reads_a_whole_krc_line():
    assert utils.kana_tokens("季節[き,せつ]は移[うつ]ろい") == ["ki", "se", "tsu", "ha", "u", "tsu", "ro", "i"]
    assert utils.kana_tokens("映[うつ]し出[だ]す") == ["u", "tsu", "shi", "da", "su"]


def test_kana_tokens_keeps_latin_and_digits_and_drops_the_rest():
    assert utils.kana_tokens("hello 世界") == ["hello"]
    assert utils.kana_tokens("その先へ 3 2 1") == ["so", "no", "he", "3", "2", "1"]
    assert utils.kana_tokens("わ、を") == ["wa", "o"]
