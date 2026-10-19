from types import SimpleNamespace

import pytest

from media_cli.ui.input_result import KeyResult
from media_cli.ui.view_model import Blank, DetailLines, ViewAction, ViewSpec
from media_cli.ui.view_tui import TuiViewBackend


@pytest.mark.parametrize("key", ("ENTER", "RIGHT", "CTRL_R", "UP", "DOWN", "PAGE_UP", "BACKSPACE"))
def test_registered_blocked_keys_give_feedback_without_transition(key):
    blocked, emitted = [], []
    context = SimpleNamespace(
        view=ViewSpec(
            "source",
            "Pick",
            (),
            details=DetailLines(Blank, Blank, Blank),
            actions=(ViewAction("reload", "Reload", ("CTRL_R",)),),
        ),
        session=SimpleNamespace(navigation=object(), input_blocked=True),
        blocked=blocked.append,
        emit=lambda *args: emitted.append(args),
    )
    assert TuiViewBackend().handle_key(key, 0, context) is KeyResult.CONSUMED
    assert len(blocked) == 1
    assert emitted == []


@pytest.mark.parametrize("key", ("q", "F9", "UNKNOWN"))
def test_unrelated_input_is_consumed_silently(key):
    context = SimpleNamespace(
        view=ViewSpec("source", "Pick", (), details=DetailLines(Blank, Blank, Blank)),
        session=SimpleNamespace(navigation=object(), input_blocked=True),
        blocked=lambda action: pytest.fail("Unknown keys must not show feedback"),
    )
    assert TuiViewBackend().handle_key(key, 0, context) is KeyResult.CONSUMED
