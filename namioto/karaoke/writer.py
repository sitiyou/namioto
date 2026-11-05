# SPDX-License-Identifier: AGPL-3.0-only
"""Write a `Lyrics` model back out as `.krc` text.

A group is written between `(...)`, a single word plain; the model is what survives, and a word
that cannot be written raises `KrcError`. `.+` keeps its continuation relation. Parsed models
retain their concrete source syntax wherever edits allow it. Ordinary writes remove mapping marks
whose removal leaves the NOTE assignment unchanged; `dotted=True` preserves literal mapping.

Qt-free.
"""

from __future__ import annotations

from copy import deepcopy
from itertools import groupby

from namioto.karaoke.model import Chapter, Group, KrcError, Line, Lyrics, Unit, _has_join, unit_words, validate

SEPARATOR = "\n---\n"
META = set("#\n[]().-,{}\r\f\xa0")


def dumps(lyrics: Lyrics, *, dotted: bool = False) -> str:
    """Serialise the model to `.krc` text; a word that cannot be written raises `KrcError`.

    With `dotted`, every unit that carries an override is written with its `.N`; mapping writers use
    this to preserve literal counts. Ordinary writes remove annotations only when the resulting
    NOTE assignment is identical. Parsed models retain source formatting where edits allow it.
    """
    if lyrics.source is not None:
        from namioto.karaoke.source import restore

        return restore(lyrics.source, _canonical(lyrics, dotted=dotted))
    return _canonical(lyrics, dotted=dotted)


def _canonical(lyrics: Lyrics, *, dotted: bool = False, simplify: bool | None = None) -> str:
    lyrics = deepcopy(lyrics)
    if simplify is None:
        simplify = not dotted
    if any(_has_join(unit) for chapter in lyrics.chapters for line in chapter.lines for unit in line.units):
        validate(lyrics)
    if simplify:
        _remove_redundant_mappings(lyrics)
    if any(_has_join(unit) for chapter in lyrics.chapters for line in chapter.lines for unit in line.units):
        validate(lyrics)
    return SEPARATOR.join(_chapter_text(chapter, dotted, preserve_groups=not simplify) for chapter in lyrics.chapters)


def _remove_redundant_mappings(lyrics: Lyrics) -> None:
    """Drop mapping annotations only when the literal NOTE assignment remains identical."""
    from namioto.karaoke.sounds import _line_sounds, note_groups

    for chapter in lyrics.chapters:
        for line in chapter.lines:
            if not any(unit.override is not None or unit.join_previous for unit in _line_units(line)):
                continue
            sound_line = _line_sounds(line, 0, 0)
            expected = note_groups(line, sound_line)
            for unit in _line_units(line):
                if unit.override is None:
                    continue
                override = unit.override
                unit.override = None
                if note_groups(line, sound_line) != expected:
                    unit.override = override
            for index in range(1, len(line.units)):
                joined = line.units[index]
                if not joined.join_previous:
                    continue
                previous = line.units[index - 1]
                overrides = [unit for unit in _nested_units(previous) if unit.override is not None]
                if not overrides:
                    continue
                saved = [(unit, unit.override) for unit in overrides]
                for unit, _ in saved:
                    unit.override = None
                joined.join_previous = False
                if note_groups(line, sound_line) != expected:
                    joined.join_previous = True
                    for unit, override in saved:
                        unit.override = override


def _nested_units(unit: Unit):
    yield unit
    if unit.ruby is not None:
        for part in unit.ruby.parts:
            for inner in part:
                yield from _nested_units(inner)


def _line_units(line: Line):
    for unit in line.units:
        yield from _nested_units(unit)


def _chapter_text(chapter: Chapter, dotted: bool = False, *, preserve_groups: bool = False) -> str:
    return "\n".join(_line_text(line, dotted, preserve_groups=preserve_groups) for line in chapter.lines)


def _line_text(line: Line, dotted: bool = False, *, preserve_groups: bool = False) -> str:
    return _owned_text(
        (_unit_text(unit, dotted, ownership=_channel(unit) is None, preserve_groups=preserve_groups), _channel(unit))
        for unit in line.units
    )


def _channel(unit: Unit) -> tuple[int, int | None] | None:
    channels = {(word.channel, word.color_channel) for word in unit_words(unit)}
    return next(iter(channels)) if len(channels) == 1 else None


def _owned_text(parts) -> str:
    result = []
    for channel, run in groupby(parts, key=lambda part: part[1]):
        text = "".join(part[0] for part in run)
        if channel is not None:
            owner, color = channel
            number = color if color is not None else owner
            if not 0 <= number < 16:
                raise KrcError("a channel must be between 0 and 15")
            if color is not None:
                text = f"@c{color}{{{text}}}"
            if owner:
                text = f"@{owner}{{{text}}}"
        result.append(text)
    return "".join(result)


def _unit_text(unit: Unit, dotted: bool = False, *, ownership: bool = True, preserve_groups: bool = False) -> str:
    _check_text(unit.text)
    if ownership and _channel(unit) is not None:
        inner = _unit_text(unit, dotted, ownership=False, preserve_groups=preserve_groups)
        return _owned_text([(inner, _channel(unit))])
    words = unit.base.words if isinstance(unit.base, Group) else [unit.base]
    base = _owned_text((word.char, (word.channel, word.color_channel)) for word in words) if ownership else unit.text
    text = f"({base})" if _needs_group(unit, preserve_groups=preserve_groups) else base
    if unit.ruby is not None:
        ruby = ",".join(
            _ruby_text(part, dotted, ownership=ownership, preserve_groups=preserve_groups)
            for part in unit.ruby.parts
        )
        text += f"[{ruby}]"
    if unit.join_previous:
        text += ".+"
    elif unit.override is not None and (dotted or unit.mora != unit.natural_mora):
        text += f".{unit.mora}"
    return text


def _needs_group(unit: Unit, *, preserve_groups: bool = False) -> bool:
    """Whether the text must be parenthesised to come back as one unit."""
    if (
        not preserve_groups
        and isinstance(unit.base, Group)
        and unit.ruby is None
        and unit.override is None
        and not unit.join_previous
    ):
        return False
    if isinstance(unit.base, Group) and unit.base.explicit:
        return preserve_groups or unit.ruby is not None or unit.override is not None or unit.join_previous
    if len(unit.text) <= 1:
        return False
    if unit.override is not None or unit.join_previous:
        return True
    if unit.is_latin():
        return False
    return not (unit.ruby is not None and unit.is_kanji())


def _ruby_text(
    part: list[Unit], dotted: bool = False, *, ownership: bool = True, preserve_groups: bool = False
) -> str:
    if not part:
        raise KrcError("a ruby part may not be empty")
    return "".join(
        _unit_text(inner, dotted, ownership=ownership, preserve_groups=preserve_groups) for inner in part
    )


def _check_text(text: str) -> None:
    if not text:
        raise KrcError("a word's text may not be empty")
    forbidden = META.intersection(text)
    if forbidden:
        raise KrcError(f"a word's text may not hold {sorted(forbidden)!r}")
