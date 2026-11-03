"""History measurements must be bounded debug data, never UI behavior."""

import logging
from dataclasses import replace

import pytest

from media_cli.app.history_diagnostics import HistoryDiagnostics
from media_cli.app.history_menu_controller import HistoryMenuController
from media_cli.app.history_menu_view import build_history_view
from media_cli.ui.footer import FooterStatus, StatusKind
from media_cli.ui.view_session import UpdateFooter


def test_disabled_diagnostics_do_not_read_clock_or_log(caplog):
    logger = logging.getLogger('test.history.disabled')
    logger.setLevel(logging.INFO)
    def clock():
        pytest.fail('Disabled diagnostics read the clock')
    diagnostics = HistoryDiagnostics(logger=logger, clock=clock)
    with diagnostics.phase('merge'):
        diagnostics.count('accepted', 3)
    diagnostics.report(force=True)
    assert not caplog.records


def test_phase_totals_counts_and_periodic_flush(caplog, capsys):
    now = [0.0]
    logger = logging.getLogger('test.history.enabled')
    with caplog.at_level(logging.DEBUG, logger=logger.name):
        diagnostics = HistoryDiagnostics(logger=logger, clock=lambda: now[0])
        with diagnostics.phase('merge'):
            now[0] = 0.004
        diagnostics.count('accepted', 3)
        diagnostics.report()
        assert not caplog.records
        now[0] = 1.2
        diagnostics.report()
        assert len(caplog.records) == 1
        assert 'accepted=3' in caplog.text
        assert 'merge_count=1' in caplog.text
        assert 'merge_total_ms=4.000' in caplog.text
        assert 'merge_max_ms=4.000' in caplog.text
        diagnostics.report(force=True)
        assert len(caplog.records) == 1
    assert capsys.readouterr() == ('', '')


def test_failed_phase_retains_timing_without_exception_text(caplog):
    now = [0.0]
    logger = logging.getLogger('test.history.failed')
    with caplog.at_level(logging.DEBUG, logger=logger.name):
        diagnostics = HistoryDiagnostics(logger=logger, clock=lambda: now[0])
        with pytest.raises(ValueError):
            with diagnostics.phase('persist'):
                now[0] = 0.025
                raise ValueError('private-title-query-url')
        diagnostics.report(force=True)
    assert 'persist_max_ms=25.000' in caplog.text
    assert 'private-title-query-url' not in caplog.text


def test_instrumentation_counts_footer_updates_without_replacing_picker(caplog):
    view = build_history_view(
        [dict(show=dict(_id='one', _provider='example', name='Fixture', _local_progress=0),
              translation_type='sub', timestamp=1)],
        mode='All', total_count=1, now=1, show_cover=False, incognito=True,
    )
    frame = ['a']
    def read():
        return replace(view, footer_statuses=(FooterStatus('Refreshing', StatusKind.LOADING, frame[0]),))
    with caplog.at_level(logging.DEBUG, logger='media_cli.app.history_diagnostics'):
        diagnostics = HistoryDiagnostics(clock=lambda: 0.0)
        controller = HistoryMenuController(
            read_view=read, switch_mode=lambda *a: None, select=lambda *a: False,
            refresh_title=lambda *a: None, refresh_view=lambda: None, delete=None,
            information=lambda *a: None, diagnostics=diagnostics,
        )
        selected = controller.session.position.selected_id
        for value in 'bcdef':
            frame[0] = value
            commands = controller.updates(force=True)
            assert len(commands) == 1 and isinstance(commands[0], UpdateFooter)
            controller.session.apply(commands[0])
        diagnostics.report(force=True)
    assert controller.session.version == 0
    assert controller.session.position.selected_id == selected
    assert 'footer_updates=5' in caplog.text
    assert 'polls=5' in caplog.text
    assert 'Fixture' not in caplog.text and 'Refreshing' not in caplog.text


@pytest.mark.parametrize('fails', [False, True])
def test_menu_exit_flushes_remaining_measurements(monkeypatch, caplog, fails):
    from media_cli.app import history_menu_controller as menu
    from media_cli.context import CliFlags, UiState

    view = build_history_view([], mode='All', total_count=0, incognito=True, show_cover=False)
    with caplog.at_level(logging.DEBUG, logger='media_cli.app.history_diagnostics'):
        diagnostics = HistoryDiagnostics(clock=lambda: 0.0)
        def run(*args, **kwargs):
            diagnostics.count('polls')
            if fails:
                raise KeyboardInterrupt
            return menu.HistoryMenuResult('quit')
        monkeypatch.setattr(menu, 'run_view_session', run)
        def open_menu():
            return menu.run_history_menu(
                CliFlags(), UiState(), read_view=lambda: view, switch_mode=lambda *a: None,
                select=lambda *a: False, refresh_title=lambda *a: None, refresh_view=lambda: None,
                delete=None, information=lambda *a: None, diagnostics=diagnostics,
            )
        if fails:
            with pytest.raises(KeyboardInterrupt):
                open_menu()
        else:
            assert open_menu().kind == 'quit'
    assert 'polls=1' in caplog.text
