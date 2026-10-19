import logging
import threading
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from media_cli.core import reporting
from media_cli.ui.transition import PreparationCancelled, run_preparation


@pytest.mark.parametrize("consumed", [False, True])
def test_error_reports_remain_diagnosable_without_duplicate_terminal_output(monkeypatch, caplog, capsys, consumed):
    messages = []
    monkeypatch.setattr(reporting, "_status_sink", lambda message, color: messages.append(message) or consumed)
    with caplog.at_level(logging.DEBUG, logger="media_cli.reporting"):
        reporting.err("\033[31mCould not start proxy\033[0m")
    assert messages == ["[ERR] Could not start proxy"]
    records = [record for record in caplog.records if record.name == "media_cli.reporting"]
    assert len(records) == 1
    assert records[0].levelno == (logging.DEBUG if consumed else logging.ERROR)
    assert "Could not start proxy" in records[0].getMessage()
    assert "\033" not in records[0].getMessage()
    assert capsys.readouterr() == ("", "")


def test_ui_error_diagnostic_is_bounded(monkeypatch, caplog):
    monkeypatch.setattr(reporting, "_status_sink", lambda *args: True)
    with caplog.at_level(logging.DEBUG, logger="media_cli.reporting"):
        reporting.err("x" * 2000)
    assert caplog.records[-1].getMessage() == "UI error report: " + "x" * 500


def test_scoped_reporting_restores_nested_sink(monkeypatch):
    global_messages, outer, inner = [], [], []
    monkeypatch.setattr(reporting, "_status_sink", lambda message, color: global_messages.append(message) or True)
    with reporting.status_sink_scope(lambda message, color: outer.append(message) or True):
        reporting.info("outer")
        with reporting.status_sink_scope(lambda message, color: inner.append(message) or True):
            reporting.info("inner")
        reporting.info("outer again")
    reporting.info("global")
    assert outer == ["[INFO] outer", "[INFO] outer again"]
    assert inner == ["[INFO] inner"]
    assert global_messages == ["[INFO] global"]


def test_abandoned_worker_reports_only_to_its_original_scope(monkeypatch):
    original, newer = [], []
    started, release, finished = threading.Event(), threading.Event(), threading.Event()

    def work():
        started.set()
        try:
            assert release.wait(2)
            reporting.warn("late old worker")
        finally:
            finished.set()

    try:
        with reporting.status_sink_scope(lambda message, color: original.append(message) or True):
            with pytest.raises(PreparationCancelled):
                run_preparation(work, tick=lambda: None, cancelled=started.is_set)
        monkeypatch.setattr(reporting, "_status_sink", lambda message, color: newer.append(message) or True)
        release.set()
        assert finished.wait(1)
        assert original == ["[WARN] late old worker"]
        assert newer == []
    finally:
        release.set()


def test_helper_does_not_capture_reports_from_child_ui(monkeypatch):
    from io import StringIO

    from test_loading_preparation import Keys

    from media_cli.ui import display, loading_preparation, terminal_runtime
    from media_cli.ui.transition import call_on_ui_thread

    class Output(StringIO):
        def isatty(self):
            return True

    @contextmanager
    def borrow():
        yield SimpleNamespace(input_bindings=None, fileno=lambda: 10, assert_owner_thread=lambda: None)

    monkeypatch.setattr(display.FLAGS, "plain_mode", False)
    monkeypatch.setattr(display.sys, "stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr(display.sys, "stdout", Output())
    monkeypatch.setattr(display, "enter_alt_screen", lambda: None)
    monkeypatch.setattr(display, "is_alt_screen_active", lambda: False)
    monkeypatch.setattr(terminal_runtime, "borrow_terminal", borrow)
    monkeypatch.setattr(loading_preparation, "LoadingInput", Keys)
    child_messages = []

    def child():
        parent_sink = reporting._status_sink
        try:
            reporting.set_status_sink(lambda message, color: child_messages.append(message) or True)
            reporting.info("child screen")
        finally:
            reporting.set_status_sink(parent_sink)

    display.with_loading("Parent work", lambda: call_on_ui_thread(child))
    assert child_messages == ["[INFO] child screen"]
