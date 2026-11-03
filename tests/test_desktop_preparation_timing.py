"""Preparation diagnostics reveal latency without exposing request data."""

import logging

import pytest

from media_cli.playback import desktop


@pytest.mark.parametrize('fails', [False, True])
def test_preparation_timing_records_success_and_failure_privately(monkeypatch, caplog, fails):
    clock = iter([10.0, 26.2])
    monkeypatch.setattr(desktop.time, 'perf_counter', lambda: next(clock))
    tracks = [{'url': 'https://example.com/sub?secret=token'}]

    def prepare(items):
        if fails:
            raise RuntimeError('https://example.com/sub?secret=token')
        return items

    with caplog.at_level(logging.DEBUG, logger=desktop.__name__):
        if fails:
            with pytest.raises(RuntimeError):
                desktop._prepare_with_timing('subtitle-filter', prepare, tracks)
        else:
            assert desktop._prepare_with_timing('subtitle-filter', prepare, tracks) is tracks
    assert 'stage=subtitle-filter' in caplog.text
    assert 'elapsed=16.200s' in caplog.text
    assert ('outcome=RuntimeError' if fails else 'outcome=ok') in caplog.text
    assert 'secret=token' not in caplog.text
