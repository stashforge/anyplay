"""Shared mpv timings describe readiness, not just extraction success."""

import json
import logging
from types import SimpleNamespace

import pytest

from media_cli.playback import mpv


@pytest.mark.parametrize('mirror', ['Rumble', 'Odysee', 'Dailymotion'])
def test_each_mirror_logs_loaded_and_first_ready_once(monkeypatch, caplog, mirror):
    player, clock, queue = setup_player(monkeypatch)
    queue.extend([
        {'event': 'file-loaded'}, {'event': 'playback-restart'},
        {'event': 'seek'}, {'event': 'playback-restart'},
        {'event': 'end-file', 'reason': 'quit'},
    ])
    clock[0] = 102.0  # Include time between loadfile and entering the wait loop.
    with caplog.at_level(logging.DEBUG, logger=mpv.__name__):
        player.wait_for_playback({'mirror': mirror, 'quality': '1080p'}, '1', 1, None)
    records = [r.getMessage() for r in caplog.records if 'mpv startup timing:' in r.getMessage()]
    assert len(records) == 2
    assert 'stage=loaded elapsed=3.000s' in records[0]
    assert 'stage=ready elapsed=4.000s' in records[1]
    assert all(f'mirror={mirror!r}' in row and "quality='1080p'" in row for row in records)


def test_failed_start_has_timing_without_leaking_stream_urls(monkeypatch, caplog):
    player, clock, queue = setup_player(monkeypatch)
    queue.append({'event': 'end-file', 'reason': 'error', 'file_error': 'loading failed'})
    with caplog.at_level(logging.DEBUG, logger=mpv.__name__):
        result, _ = player.wait_for_playback({'mirror': 'https://example.com/?secret=value', 'quality': 'x'*1000}, '1', 1, None)
    assert result == 'ERROR'
    records = [r.getMessage() for r in caplog.records if 'mpv startup timing:' in r.getMessage()]
    assert len(records) == 1
    assert 'stage=failed elapsed=1.000s' in records[0]
    assert 'secret=value' not in records[0]
    assert len(records[0]) < 250


def setup_player(monkeypatch):
    clock = [100.0]
    queue = []
    player = mpv.MpvIpc()
    player.running = True
    player.process = SimpleNamespace(poll=lambda: None)

    def recv(size):
        if not queue:
            raise BlockingIOError
        return (json.dumps(queue.pop(0)) + '\n').encode()

    def select(*args):
        clock[0] += 1.0
        if clock[0] >= getattr(player, '_test_quit_at', float('inf')) and not queue:
            queue.append({'event': 'end-file', 'reason': 'quit'})
        return ([player.client] if queue else [], [], [])

    player.client = SimpleNamespace(recv=recv)
    monkeypatch.setattr(player, 'start', lambda: None)
    monkeypatch.setattr(player, 'send_cmd', lambda *args: None)
    monkeypatch.setattr(mpv.sys, 'stdin', SimpleNamespace(fileno=lambda: 0))
    monkeypatch.setattr(mpv.os, 'isatty', lambda fd: False)
    monkeypatch.setattr(mpv.select, 'select', select)
    monkeypatch.setattr(mpv.time, 'monotonic', lambda: clock[0])
    player.load('https://cdn.example.com/video.m3u8?private=token', 'Example', {}, '')
    return player, clock, queue


def test_timing_resets_on_each_mirror_attempt(monkeypatch, caplog):
    player, clock, queue = setup_player(monkeypatch)
    with caplog.at_level(logging.DEBUG, logger=mpv.__name__):
        for mirror in ('First', 'Fallback'):
            player.load('https://cdn.example.com/video.m3u8', 'Example', {}, '')
            queue.extend([{'event': 'file-loaded'},
                          {'event': 'property-change', 'name': 'playback-time', 'data': 1},
                          {'event': 'playback-restart'}, {'event': 'end-file', 'reason': 'quit'}])
            player.wait_for_playback({'mirror': mirror}, '1', 1, None)
    records = [r.getMessage() for r in caplog.records if 'mpv startup timing:' in r.getMessage()]
    assert len(records) == 4
    assert sum('stage=ready elapsed=2.000s' in row for row in records) == 2
    assert sum('event=first-position' in row for row in records) == 2


def test_slow_start_records_user_exit_without_timeout_failure(monkeypatch, caplog):
    player, clock, queue = setup_player(monkeypatch)
    player._test_quit_at = 170
    with caplog.at_level(logging.DEBUG, logger=mpv.__name__):
        result, _ = player.wait_for_playback({'mirror': 'Slow'}, '1', 1, None)
    assert result == 'QUIT'
    records = [r.getMessage() for r in caplog.records if 'mpv startup timing:' in r.getMessage()]
    assert len(records) == 1
    assert 'stage=cancelled' in records[0]
    assert clock[0] >= 170


@pytest.mark.parametrize('matching_path,position', [(True, 2.0), (False, 2.0), (True, 0.0)])
def test_startup_queries_confirm_only_current_playing_file(monkeypatch, caplog, matching_path, position):
    player, clock, queue = setup_player(monkeypatch)
    requests = []

    def sendall(payload):
        request = json.loads(payload)
        requests.append(request)
        if len(requests) == 1 and not matching_path:
            # Replies from a previous load must not make the replacement ready.
            queue.extend([
                {'request_id': 'startup:0:0:path', 'error': 'success',
                 'data': 'https://cdn.example.com/video.m3u8?private=token'},
                {'request_id': 'startup:0:0:time-pos', 'error': 'success', 'data': 99.0},
            ])
        name = request['command'][1]
        value = position if name == 'time-pos' else (
            'https://cdn.example.com/video.m3u8?private=token' if matching_path else 'https://old.example/video'
        )
        queue.append({'request_id': request['request_id'], 'error': 'success', 'data': value})
        if len(requests) == 2:
            queue.append({'event': 'end-file', 'reason': 'quit'})

    player.client.sendall = sendall
    with caplog.at_level(logging.DEBUG, logger=mpv.__name__):
        result, _ = player.wait_for_playback({'mirror': 'Dailymotion'}, '1', 1, None)
    if matching_path and position > 0:
        assert result == 'QUIT'
        assert any('event=state-query' in r.getMessage() for r in caplog.records)
        assert not any('startup timed out' in r.getMessage() for r in caplog.records)
    else:
        assert result == 'QUIT'
        assert not any('stage=ready' in r.getMessage() for r in caplog.records)
    assert requests
    assert 'private=token' not in caplog.text


def test_redraw_failure_does_not_hide_positive_playback_position(monkeypatch, caplog):
    player, clock, queue = setup_player(monkeypatch)

    def redraw(props):
        raise RuntimeError('render failure')

    player.redraw_callback = redraw
    queue.extend([{'event': 'property-change', 'name': 'playback-time', 'data': 2.0},
                  {'event': 'end-file', 'reason': 'quit'}])
    with caplog.at_level(logging.DEBUG, logger=mpv.__name__):
        result, _ = player.wait_for_playback({'mirror': 'Test'}, '1', 1, None)
    assert result == 'QUIT'
    assert any('event=first-position' in r.getMessage() for r in caplog.records)
    assert any('redraw failed' in r.getMessage() for r in caplog.records)
