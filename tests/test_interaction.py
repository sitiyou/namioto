# SPDX-License-Identifier: AGPL-3.0-only
"""The mode-and-tool value: which states exist and how a user action moves between them."""

from __future__ import annotations

import pytest

from namioto.interaction import Interaction, Mode, Tool, pick_tool, toggle_mode


def test_viewing_has_no_tool_and_editing_always_carries_one() -> None:
    assert Interaction.viewing() == Interaction(Mode.VIEW, None)
    assert Interaction.editing_with(Tool.PEN) == Interaction(Mode.EDIT, Tool.PEN)
    with pytest.raises(ValueError):
        Interaction(Mode.EDIT, None)
    with pytest.raises(ValueError):
        Interaction(Mode.VIEW, Tool.PEN)


def test_the_mode_button_toggles_and_enters_on_the_pen() -> None:
    editing = toggle_mode(Interaction.viewing())
    assert editing.editing and editing.tool is Tool.PEN
    assert toggle_mode(editing) == Interaction.viewing()


def test_picking_a_tool_enters_editing_with_it() -> None:
    assert pick_tool(Interaction.viewing(), Tool.SELECT) == Interaction(Mode.EDIT, Tool.SELECT)
