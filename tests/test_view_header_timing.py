"""Slow header diagnostics split presentation work without exposing content."""

import logging

from test_ui_view_presentation import setup

from media_cli.ui import view_presentation
from media_cli.ui.view_model import ImageSpec


def test_slow_header_reports_cover_and_footer_costs_without_content(monkeypatch, caplog, capsys):
    presentation, _, manager = setup(image=ImageSpec('private-image-id', 'available', 'https://example.test/private'))
    now = [0.0]
    get, footer = manager.get, manager.footer_line
    def slow_get(show):
        result = get(show)
        now[0] += 0.125
        return result
    def slow_footer(*args, **kwargs):
        result = footer(*args, **kwargs)
        now[0] += 0.010
        return result
    manager.get, manager.footer_line = slow_get, slow_footer
    monkeypatch.setattr(view_presentation.time, 'perf_counter', lambda: now[0])
    with caplog.at_level(logging.DEBUG, logger=view_presentation.__name__):
        header = presentation.details('a')
    assert 'Loading cover' in header
    assert 'total=0.135s' in caplog.text
    assert 'cover_lookup=0.125s' in caplog.text
    assert 'footer=0.010s' in caplog.text
    assert 'private' not in caplog.text and 'example.test' not in caplog.text
    assert capsys.readouterr() == ('', '')


def test_normal_header_does_not_read_diagnostic_clock(monkeypatch):
    presentation, _, _ = setup()
    def forbidden():
        raise AssertionError('Disabled timing read clock')
    monkeypatch.setattr(view_presentation.time, 'perf_counter', forbidden)
    logger = logging.getLogger(view_presentation.__name__)
    previous = logger.level
    logger.setLevel(logging.INFO)
    try:
        assert isinstance(presentation.details('a'), str)
    finally:
        logger.setLevel(previous)
