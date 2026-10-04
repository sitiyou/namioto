# SPDX-License-Identifier: AGPL-3.0-only
"""The lyric mapping's core: the target NOTE stream, the raw evidence, and the mapping over them."""

from namioto.lyricmap.confidence import Reading, read
from namioto.lyricmap.faithful import consumes
from namioto.lyricmap.faithful import read as read_faithful
from namioto.lyricmap.notes import Branch, Resolved, components, ordered, overlaps, resolve
from namioto.lyricmap.problems import MappingError, Problem
from namioto.lyricmap.raw import Raw, from_spans, snap_to_beats, validate
from namioto.lyricmap.solver import fit_errors, solve
from namioto.lyricmap.spans import sound_spans
from namioto.lyricmap.verify import Gate, faithful_gate, verify

__all__ = [
    "Branch",
    "Gate",
    "MappingError",
    "Problem",
    "Raw",
    "Reading",
    "Resolved",
    "components",
    "consumes",
    "fit_errors",
    "faithful_gate",
    "from_spans",
    "ordered",
    "overlaps",
    "read",
    "read_faithful",
    "resolve",
    "snap_to_beats",
    "solve",
    "sound_spans",
    "validate",
    "verify",
]
