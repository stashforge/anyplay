from contextlib import contextmanager
from io import StringIO
from types import SimpleNamespace

import pytest

from media_cli.core import reporting
from media_cli.ui import display, loading_preparation, terminal_runtime, transition
from media_cli.ui.loading_model import LoadingInteraction, LoadingPlacement
from media_cli.ui.picker_actions import picker_bindings


@pytest.mark.parametrize("loader", ("with_loading", "with_anilist_menu_loading"))
@pytest.mark.parametrize("fails", (False, True))
@pytest.mark.parametrize("explicit_callback", (False, True))
@pytest.mark.parametrize("message", ("Fetch", ["Fetch", "Getting things ready"]))
def test_loading_helpers_delegate_work_and_status_to_one_driver(monkeypatch, loader, fails, explicit_callback, message):
    class Output(StringIO):
        def isatty(self):
            return True

    source = picker_bindings(refresh=True)
    runtime = SimpleNamespace(
        input_bindings=source, fileno=lambda: 10, assert_owner_thread=lambda: None,
    )

    @contextmanager
    def borrow():
        yield runtime

    drivers, statuses = [], []

    class Driver:
        def __init__(self, spec, **kwargs):
            self.spec, self.options = spec, kwargs
            drivers.append(self)

        def update(self, message):
            statuses.append(message)

        def run(self, prepare):
            return prepare(self.update)

    monkeypatch.setattr(display.FLAGS, "plain_mode", False)
    monkeypatch.setattr(display.sys, "stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr(display.sys, "stdout", Output())
    monkeypatch.setattr(display, "enter_alt_screen", lambda: None)
    monkeypatch.setattr(terminal_runtime, "borrow_terminal", borrow)
    monkeypatch.setattr(loading_preparation, "LoadingPreparation", Driver)
    monkeypatch.setattr(transition, "run_preparation", lambda *a, **k: pytest.fail("helper bypassed shared driver"))
    previous_sink = reporting._status_sink

    def work(status_cb):
        status_cb("Newest task")
        assert reporting._status_sink is not previous_sink
        if fails:
            raise ValueError("work failed")
        return 42

    args = ("Watching", message, work) if loader == "with_anilist_menu_loading" else (message, work)
    explicit_statuses = []
    kwargs = {"status_cb": explicit_statuses.append} if explicit_callback else {}
    if fails:
        with pytest.raises(ValueError, match="work failed"):
            getattr(display, loader)(*args, **kwargs)
    else:
        assert getattr(display, loader)(*args, **kwargs) == 42
    assert len(drivers) == 1
    spec = drivers[0].spec
    assert spec.message == (tuple(message) if isinstance(message, list) else message)
    assert spec.interaction is LoadingInteraction.BLOCKING
    assert spec.placement is LoadingPlacement.FOOTER
    assert spec.actions is source
    preserved = explicit_callback and loader == "with_anilist_menu_loading"
    assert statuses == ([] if preserved else ["Newest task"])
    assert explicit_statuses == (["Newest task"] if preserved else [])
    assert reporting._status_sink is previous_sink


@pytest.mark.parametrize("loader", ("with_loading", "with_anilist_menu_loading"))
def test_plain_helpers_do_not_create_loading_driver(monkeypatch, loader):
    monkeypatch.setattr(display.FLAGS, "plain_mode", True)
    monkeypatch.setattr(loading_preparation, "LoadingPreparation", lambda *a, **k: pytest.fail("native loader"))
    args = ("Watching", "Fetch", lambda: 42) if loader == "with_anilist_menu_loading" else ("Fetch", lambda: 42)
    assert getattr(display, loader)(*args) == 42


@pytest.mark.parametrize("loader", ("with_loading",))
def test_nested_helper_updates_existing_scope_without_second_driver(monkeypatch, loader):
    statuses = []
    monkeypatch.setattr(transition, "in_preparation", lambda: True)
    monkeypatch.setattr(transition, "set_preparation_status", statuses.append)
    monkeypatch.setattr(loading_preparation, "LoadingPreparation", lambda *a, **k: pytest.fail("second loader"))

    def work(status_cb):
        status_cb("Nested progress")
        return 42

    args = ("Watching", "Nested work", work) if loader == "with_anilist_menu_loading" else ("Nested work", work)
    assert getattr(display, loader)(*args) == 42
    assert statuses == ["Nested work", "Nested progress"]


def test_legacy_callback_normalizes_at_boundary_not_in_typed_driver():
    statuses = []

    def work(status_cb):
        for value in ("", None, 0, "   ", "\x1b[31mReading\x1b[0m", 42):
            status_cb(value)

    display._invoke_loading_work(work, (), {}, statuses.append)
    assert statuses == ["Reading", "42"]


def test_callback_accepts_message_alternatives_without_stringifying():
    statuses = []
    alternatives = ["Reading", "Getting things ready"]
    display._invoke_loading_work(lambda status_cb: status_cb(alternatives), (), {}, statuses.append)
    assert statuses == [alternatives]
