# SPDX-License-Identifier: AGPL-3.0-only
"""Karaoke: the `.krc` lyric parser, and the renderers built on it."""

from namioto.karaoke.parser import Chapter, Line, Lyrics, Word, parse

__all__ = ["Chapter", "Line", "Lyrics", "Word", "parse"]
