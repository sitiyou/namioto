# SPDX-License-Identifier: AGPL-3.0-only
"""What the roll does with the pointer and the keys, as one explicit value.

The editor is either viewing - the roll is a seek bar over the spectrum - or editing with one
tool in hand. The two are one value because every behaviour that depends on them has to see them
together: a state that says "editing with no tool" or "viewing with a tool" never exists, so no
caller has to guard against it. A new behaviour reads this value, and there is one function per
user action that makes the next one.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Mode(Enum):
    VIEW = "view"
    EDIT = "edit"


class Tool(Enum):
    PEN = "pen"
    SELECT = "select"


@dataclass(frozen=True)
class Interaction:
    """The mode and, while editing, the tool."""

    mode: Mode = Mode.VIEW
    tool: Tool | None = None

    def __post_init__(self) -> None:
        if (self.mode is Mode.EDIT) != (self.tool is not None):
            raise ValueError("editing carries a tool and viewing has none")

    @property
    def editing(self) -> bool:
        return self.mode is Mode.EDIT

    @classmethod
    def viewing(cls) -> Interaction:
        return cls()

    @classmethod
    def editing_with(cls, tool: Tool) -> Interaction:
        return cls(Mode.EDIT, tool)


def toggle_mode(state: Interaction) -> Interaction:
    """The edit-mode button: leaving goes to the view, entering starts on the pen."""
    return Interaction.viewing() if state.editing else Interaction.editing_with(Tool.PEN)


def pick_tool(state: Interaction, tool: Tool) -> Interaction:
    """Picking a tool turns editing on and leaves that tool in hand."""
    return Interaction.editing_with(tool)
