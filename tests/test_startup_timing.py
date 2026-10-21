"""Startup measurements finish at the first rendered frame, once per launch."""

import logging


def test_first_frame_logs_phase_durations_without_sensitive_values(monkeypatch, caplog):
    from media_cli.core import startup

    monkeypatch.setattr(startup, "_started", None)
    monkeypatch.setattr(startup, "_phases", ())
    now = {"value": 10.0}
    monkeypatch.setattr(startup.time, "monotonic", lambda: now["value"])
    startup.begin_startup()
    now["value"] = 10.1
    startup.startup_checkpoint("imports")
    now["value"] = 10.3
    startup.startup_checkpoint("configuration")
    now["value"] = 10.4
    with caplog.at_level(logging.DEBUG, logger="media_cli.core.startup"):
        startup.startup_first_frame()
        startup.startup_first_frame()
    messages = [record.message for record in caplog.records if record.name == "media_cli.core.startup"]
    assert len(messages) == 1
    assert all(part in messages[0] for part in ("total=400.0ms", "imports=100.0ms", "configuration=200.0ms", "first_frame=100.0ms"))


def test_imported_widget_without_cli_startup_does_not_measure(monkeypatch, caplog):
    from media_cli.core import startup

    monkeypatch.setattr(startup, "_started", None)
    with caplog.at_level(logging.DEBUG, logger="media_cli.core.startup"):
        startup.startup_checkpoint("configuration")
        startup.startup_first_frame()
    assert not caplog.records
