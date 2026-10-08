import logging
import os
from types import SimpleNamespace

from media_cli.cli.args import parse_cli_args


def test_configure_file_logging_writes_warning_and_rotates(monkeypatch, isolate_test_state_and_config):
    from media_cli.core.diagnostics import configure_file_logging, get_log_paths, reset_file_logging_for_tests
    from media_cli.state import paths

    monkeypatch.setattr(paths, "LOG_DIR", os.path.join(isolate_test_state_and_config, "logs"))
    reset_file_logging_for_tests()
    try:
        configure_file_logging(debug=False, max_bytes=220, backup_count=1)
        logger = logging.getLogger("media_cli.tests")

        logger.debug("hidden debug message")
        logger.warning("first warning")
        logger.warning("second warning with enough padding to rotate the file")
        logger.error("third error")

        log_paths = get_log_paths()
        assert os.path.exists(log_paths["anyplay"])
        assert os.path.exists(log_paths["anyplay"] + ".1")

        combined = ""
        for path in (log_paths["anyplay"], log_paths["anyplay"] + ".1"):
            with open(path, "r", encoding="utf-8") as fh:
                combined += fh.read()

        assert "first warning" in combined
        assert "third error" in combined
        assert "hidden debug message" not in combined
    finally:
        reset_file_logging_for_tests()


def test_configure_file_logging_debug_mode_records_debug(monkeypatch, isolate_test_state_and_config):
    from media_cli.core.diagnostics import configure_file_logging, get_log_paths, reset_file_logging_for_tests
    from media_cli.state import paths

    monkeypatch.setattr(paths, "LOG_DIR", os.path.join(isolate_test_state_and_config, "logs"))
    reset_file_logging_for_tests()
    try:
        configure_file_logging(debug=True, max_bytes=1024, backup_count=1)
        logging.getLogger("media_cli.tests").debug("visible debug message")

        with open(get_log_paths()["anyplay"], "r", encoding="utf-8") as fh:
            assert "visible debug message" in fh.read()
    finally:
        reset_file_logging_for_tests()


def test_write_exception_log_always_records_traceback(monkeypatch, isolate_test_state_and_config):
    from media_cli.core import storage
    from media_cli.core.diagnostics import reset_file_logging_for_tests
    from media_cli.state import paths

    monkeypatch.setattr(paths, "LOG_DIR", os.path.join(isolate_test_state_and_config, "logs"))
    reset_file_logging_for_tests()
    try:
        try:
            raise RuntimeError("normal mode crash")
        except RuntimeError:
            log_path = storage.write_exception_log("crash.log")

        assert log_path
        with open(log_path, "r", encoding="utf-8") as fh:
            content = fh.read()
        assert "RuntimeError: normal mode crash" in content
    finally:
        reset_file_logging_for_tests()


def test_episode_fetch_failure_does_not_write_traceback_to_stderr(monkeypatch, capsys, caplog):
    from media_cli.services import catalog

    class FailingProvider:
        def episode_sources(self, show_id, ep, ttype):
            raise TimeoutError("network read timed out")

    monkeypatch.setattr(catalog, "get_provider", lambda provider_id, request_fn: FailingProvider())
    caplog.set_level(logging.ERROR)

    result = catalog.get_episode_data("show-1", "1", "sub", provider_id="probe")

    captured = capsys.readouterr()
    assert result is None
    assert captured.err == ""
    assert "Traceback" not in captured.out
    assert any("Episode fetch failed" in record.getMessage() for record in caplog.records)


def test_logs_command_parses_tail_option():
    args, _ = parse_cli_args(["logs", "--tail", "20"])

    assert args.command == "logs"
    assert args.logs is True
    assert args.logs_tail == 20


def test_logs_command_prints_full_log_by_default(capsys, isolate_test_state_and_config):
    from media_cli.app.orchestrator import handle_logs_command
    from media_cli.core.diagnostics import get_log_paths

    log_path = get_log_paths()["anyplay"]
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, "w", encoding="utf-8") as fh:
        fh.write("first line\nsecond line\nthird line\n")

    handle_logs_command(SimpleNamespace(logs_tail=0))

    assert capsys.readouterr().out == "first line\nsecond line\nthird line\n"


def test_logs_command_tail_prints_only_requested_lines(capsys, isolate_test_state_and_config):
    from media_cli.app.orchestrator import handle_logs_command
    from media_cli.core.diagnostics import get_log_paths

    log_path = get_log_paths()["anyplay"]
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, "w", encoding="utf-8") as fh:
        fh.write("first line\nsecond line\nthird line\n")

    handle_logs_command(SimpleNamespace(logs_tail=2))

    assert capsys.readouterr().out == "second line\nthird line\n"


def test_logs_command_reports_when_log_file_missing(capsys, isolate_test_state_and_config):
    from media_cli.app.orchestrator import handle_logs_command
    from media_cli.core.diagnostics import get_log_paths

    log_path = get_log_paths()["anyplay"]

    handle_logs_command(SimpleNamespace(logs_tail=0))

    out = capsys.readouterr().out
    assert f"No anyplay log file yet: {log_path}" in out
