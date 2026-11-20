import logging
from media_cli.ui.stall_diagnostics import ListStallDiagnostics


def test_snapshot_names_blocking_stage_without_local_values(caplog):
    now = [0.]
    logger = logging.getLogger('test.ui.stall')
    with caplog.at_level(logging.DEBUG, logger=logger.name):
        watcher = ListStallDiagnostics(logger=logger, clock=lambda: now[0])
        watcher.stage('key-read')
        now[0] = .7
        watcher.sample()
        watcher.sample()
    assert caplog.text.count('UI stall snapshot:') == 1
    assert 'stage=key-read' in caplog.text
    assert 'elapsed_ms=700.0' in caplog.text
    assert 'UI:' in caplog.text


def test_normal_mode_starts_no_watch_thread():
    logger = logging.getLogger('test.ui.stall.disabled')
    logger.setLevel(logging.WARNING)
    with ListStallDiagnostics(logger=logger) as watcher:
        assert watcher.thread is None
