"""Player failure evidence is bounded debug data, not terminal output."""

import json
import logging
from types import SimpleNamespace

import pytest

from media_cli.playback import mpv


@pytest.mark.parametrize('event,reason,error,returncode,expected', [
    ('end-file', 'error', 'loading failed', None, 'ERROR'),
    ('end-file', 'error', 'unrecognized file format', 2, 'ERROR'),
    ('disconnect', '', '', -11, 'ERROR'),
    ('end-file', 'quit', '', 0, 'QUIT'),
    ('end-file', 'stop', '', None, 'QUIT'),
])
def test_playback_records_engine_error_or_ipc_disconnect(monkeypatch, caplog, event, reason, error, returncode, expected):
    player = mpv.MpvIpc()
    player.running = True
    payload = (json.dumps(dict(event=event, reason=reason, error=error)) + '\n').encode()
    player.client = SimpleNamespace(recv=lambda size: b'' if event == 'disconnect' else payload)
    player.process = SimpleNamespace(poll=lambda: returncode)
    monkeypatch.setattr(player, 'send_cmd', lambda *a: None)
    monkeypatch.setattr(mpv.sys, 'stdin', SimpleNamespace(fileno=lambda: 0))
    monkeypatch.setattr(mpv.os, 'isatty', lambda fd: False)
    monkeypatch.setattr(mpv.select, 'select', lambda *a: ([player.client], [], []))
    with caplog.at_level(logging.DEBUG, logger=mpv.__name__):
        result, _ = player.wait_for_playback({}, '1', 1, None)
    assert result == expected
    records = [record.getMessage() for record in caplog.records if 'mpv exit diagnostic:' in record.getMessage()]
    assert len(records) == 1
    assert f'process_returncode={returncode!r}' in records[0]
    assert error in records[0]
    assert ('event=ipc-disconnect' if event == 'disconnect' else 'event=end-file') in records[0]


def test_diagnostic_does_not_include_url_or_unbounded_error_detail(caplog):
    player = mpv.MpvIpc()
    with caplog.at_level(logging.DEBUG, logger=mpv.__name__):
        player._record_playback_end('end-file', reason='error', error='https://example.test/?token=private')
        player._record_playback_end('end-file', reason='error', error='x' * 5000)
    assert 'token=private' not in caplog.text
    assert 'example.test' not in caplog.text
    assert all(len(record.getMessage()) < 400 for record in caplog.records)
