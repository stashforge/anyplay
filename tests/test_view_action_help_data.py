from dataclasses import FrozenInstanceError

import pytest

from media_cli.ui.view_model import Blank, DetailLines, ViewAction, ViewSpec
from media_cli.ui.view_presentation import ViewPresentation
from media_cli.ui.view_session import ViewSession
from media_cli.ui.view_tui import TuiViewBackend


def _view(*, actions=(), help_data=()):
    return ViewSpec("test", "Test", actions=actions, details=DetailLines(Blank, Blank, Blank), help=help_data)


@pytest.mark.parametrize("value", [None, [], "text", {("Label", "Description")}, (("Label",),)])
def test_help_requires_tuple_of_two_string_tuples(value):
    with pytest.raises((TypeError, ValueError), match="help"):
        _view(help_data=value)


@pytest.mark.parametrize("entry", [("", "Description"), ("Label", " "), (1, "Description"), ("Label", None)])
def test_help_entries_require_nonblank_strings(entry):
    with pytest.raises((TypeError, ValueError), match="help"):
        _view(help_data=(entry,))


def test_help_is_immutable_and_rejects_duplicate_labels():
    with pytest.raises(ValueError, match="Duplicate.*help"):
        _view(help_data=(("Keys", "First"), ("Keys", "Second")))

    view = _view(help_data=(("Keys", "First"),))
    assert view.help == (("Keys", "First"),)
    with pytest.raises(FrozenInstanceError):
        view.help = ()


@pytest.mark.parametrize("value", [None, 0, 1, "false"])
def test_action_footer_requires_boolean(value):
    with pytest.raises(TypeError, match="footer"):
        ViewAction("refresh", "Refresh", footer=value)


def test_footer_visibility_does_not_remove_action_bindings_or_labels():
    action = ViewAction("refresh", "Refresh", ("CTRL_R",), footer=False)
    view = _view(actions=(action,))

    from media_cli.ui.tui_actions import tui_bindings

    binding = tui_bindings(view).resolve("CTRL_R")
    assert binding is not None
    assert binding.action.footer is False
    assert "[Ctrl+R] Refresh" in TuiViewBackend().action_labels(view)

    presentation = ViewPresentation(ViewSession(view), object())
    assert presentation._footer().actions == ("Esc=back",)


def test_default_action_footer_remains_visible():
    action = ViewAction("refresh", "Refresh", ("CTRL_R",))
    assert action.footer is True


@pytest.mark.parametrize("entry", [("Keys", "\033[31mRed"), ("Key\n", "Description")])
def test_help_data_cannot_inject_terminal_styling_or_controls(entry):
    with pytest.raises(ValueError, match="control"):
        _view(help_data=(entry,))
