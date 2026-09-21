# SPDX-License-Identifier: AGPL-3.0-only
"""Small helpers shared across the Qt-free modules.

`kana_tokens()` is the reading `namioto-align` takes: it turns a `.krc` line's bracketed kana into
one hepburn token per mora, the unit FA-Kara's models are trained on. It is deliberately small - a
`.krc` file already carries the kana reading of every kanji, so the kanji surface and the annotation
punctuation only have to be dropped, with no kanji lookup and no dictionary. A run of Latin letters
or digits is kept as one token; everything else is dropped, so the same call reads a bare kana
string and a whole `.krc` line.
"""

from __future__ import annotations

import unicodedata

_VOWELS = "aiueo"

# the gojuuon, dakuten and handakuten, after katakana is normalised to hiragana
_KANA = {
    "あ": "a",
    "い": "i",
    "う": "u",
    "え": "e",
    "お": "o",
    "か": "ka",
    "き": "ki",
    "く": "ku",
    "け": "ke",
    "こ": "ko",
    "が": "ga",
    "ぎ": "gi",
    "ぐ": "gu",
    "げ": "ge",
    "ご": "go",
    "さ": "sa",
    "し": "shi",
    "す": "su",
    "せ": "se",
    "そ": "so",
    "ざ": "za",
    "じ": "ji",
    "ず": "zu",
    "ぜ": "ze",
    "ぞ": "zo",
    "た": "ta",
    "ち": "chi",
    "つ": "tsu",
    "て": "te",
    "と": "to",
    "だ": "da",
    "ぢ": "ji",
    "づ": "zu",
    "で": "de",
    "ど": "do",
    "な": "na",
    "に": "ni",
    "ぬ": "nu",
    "ね": "ne",
    "の": "no",
    "は": "ha",
    "ひ": "hi",
    "ふ": "fu",
    "へ": "he",
    "ほ": "ho",
    "ば": "ba",
    "び": "bi",
    "ぶ": "bu",
    "べ": "be",
    "ぼ": "bo",
    "ぱ": "pa",
    "ぴ": "pi",
    "ぷ": "pu",
    "ぺ": "pe",
    "ぽ": "po",
    "ま": "ma",
    "み": "mi",
    "む": "mu",
    "め": "me",
    "も": "mo",
    "や": "ya",
    "ゆ": "yu",
    "よ": "yo",
    "ら": "ra",
    "り": "ri",
    "る": "ru",
    "れ": "re",
    "ろ": "ro",
    "わ": "wa",
    "ゐ": "i",
    "ゑ": "e",
    "を": "o",
    "ん": "n",
    "ゔ": "vu",
    "ぁ": "a",
    "ぃ": "i",
    "ぅ": "u",
    "ぇ": "e",
    "ぉ": "o",
    "ゃ": "ya",
    "ゅ": "yu",
    "ょ": "yo",
}

# a base kana followed by a small ya, yu or yo, which is one mora
_DIGRAPHS = {
    "きゃ": "kya",
    "きゅ": "kyu",
    "きょ": "kyo",
    "ぎゃ": "gya",
    "ぎゅ": "gyu",
    "ぎょ": "gyo",
    "しゃ": "sha",
    "しゅ": "shu",
    "しょ": "sho",
    "じゃ": "ja",
    "じゅ": "ju",
    "じょ": "jo",
    "ちゃ": "cha",
    "ちゅ": "chu",
    "ちょ": "cho",
    "ぢゃ": "ja",
    "ぢゅ": "ju",
    "ぢょ": "jo",
    "にゃ": "nya",
    "にゅ": "nyu",
    "にょ": "nyo",
    "ひゃ": "hya",
    "ひゅ": "hyu",
    "ひょ": "hyo",
    "びゃ": "bya",
    "びゅ": "byu",
    "びょ": "byo",
    "ぴゃ": "pya",
    "ぴゅ": "pyu",
    "ぴょ": "pyo",
    "みゃ": "mya",
    "みゅ": "myu",
    "みょ": "myo",
    "りゃ": "rya",
    "りゅ": "ryu",
    "りょ": "ryo",
    "ふぁ": "fa",
    "ふぃ": "fi",
    "ふぇ": "fe",
    "ふぉ": "fo",
    "うぃ": "wi",
    "うぇ": "we",
    "うぉ": "wo",
    "ゔぁ": "va",
    "ゔぃ": "vi",
    "ゔぇ": "ve",
    "ゔぉ": "vo",
    "てぃ": "ti",
    "でぃ": "di",
    "とぅ": "tu",
    "どぅ": "du",
    "しぇ": "she",
    "じぇ": "je",
    "ちぇ": "che",
}


def _normalize(text: str) -> str:
    """Full-width and half-width forms resolved, katakana folded onto hiragana."""
    folded = []
    for char in unicodedata.normalize("NFKC", text):
        code = ord(char)
        folded.append(chr(code - 0x60) if 0x30A1 <= code <= 0x30F6 else char)
    return "".join(folded)


def _geminate(mora: str) -> str:
    """A sokuon doubles the next mora's consonant; `ch` becomes `tch`, Hepburn's own spelling."""
    if mora.startswith("ch"):
        return "t" + mora
    return mora[0] + mora if mora[:1] not in _VOWELS else "t" + mora


def kana_tokens(text: str) -> list[str]:
    """The reading of `text` as one hepburn token per mora, Latin and digits kept as runs.

    `text` is a kana string or a whole `.krc` line; the kanji and the annotation punctuation are
    dropped, so `漢字[かんじ]` reads as `kanji`.
    """
    folded = _normalize(text)
    found: list[str] = []
    geminate = False
    index = 0
    while index < len(folded):
        char = folded[index]
        if char == "っ":
            geminate = True
            index += 1
            continue
        if char == "ー":
            if found and found[-1][-1:] in _VOWELS:
                found[-1] += found[-1][-1]
            index += 1
            continue
        if char.isascii() and char.isalnum():
            end = index + 1
            while end < len(folded) and folded[end].isascii() and folded[end].isalnum():
                end += 1
            found.append(folded[index:end].lower())
            index = end
            continue
        pair = folded[index : index + 2]
        if pair in _DIGRAPHS:
            mora, index = _DIGRAPHS[pair], index + 2
        elif char in _KANA:
            mora, index = _KANA[char], index + 1
        else:
            index += 1
            continue
        found.append(_geminate(mora) if geminate else mora)
        geminate = False
    return found
