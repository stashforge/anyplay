"""The existing mpv window explains slow replacement streams."""

import json
from types import SimpleNamespace

import pytest

from media_cli.playback import mpv


@pytest.mark.parametrize('previously_played,outcome', [(False, 'ready'), (True, 'ready'), (True, 'wait')])
def test_switch_loading_osd_ticks_once_per_second_then_clears(monkeypatch, previously_played, outcome):
    clock = [0.0]
    queue = []
    commands = []
    phase = [0]
    started = [0.0]
    ready_sent = [False]
    player = mpv.MpvIpc()
    player.running = True
    player.process = SimpleNamespace(poll=lambda: None)

    def recv(size):
        if queue:
            return queue.pop(0)
        raise BlockingIOError

    def select(*args):
        clock[0] += .25
        if outcome == 'ready' and phase[0] == 2 and not ready_sent[0] and clock[0] - started[0] >= 23:
            ready_sent[0] = True
            feed({'event': 'file-loaded'}, {'event': 'playback-restart'},
                 {'event': 'end-file', 'reason': 'quit'})
        if outcome == 'wait' and phase[0] == 2 and clock[0] - started[0] >= 70 and not queue:
            feed({'event': 'end-file', 'reason': 'quit'})
        return ([player.client] if queue else [], [], [])

    def feed(*events):
        queue.append((''.join(json.dumps(e)+'\n' for e in events)).encode())

    player.client = SimpleNamespace(recv=recv)
    monkeypatch.setattr(player, 'start', lambda: None)
    monkeypatch.setattr(player, 'send_cmd', lambda *args: commands.append(args))
    monkeypatch.setattr(mpv.sys, 'stdin', SimpleNamespace(fileno=lambda: 0))
    monkeypatch.setattr(mpv.os, 'isatty', lambda fd: False)
    monkeypatch.setattr(mpv.select, 'select', select)
    monkeypatch.setattr(mpv.time, 'monotonic', lambda: clock[0])
    if previously_played:
        phase[0] = 1
        player.load('https://example.com/first.m3u8', 'First', {}, '')
        feed({'event': 'file-loaded'}, {'event': 'playback-restart'},
             {'event': 'end-file', 'reason': 'error'})
        assert player.wait_for_playback({'mirror': 'First'}, '1', 1, None)[0] == 'ERROR'
    commands.clear()
    phase[0] = 2
    started[0] = clock[0]
    player.load('https://example.com/replacement.m3u8', 'Replacement', {}, '')
    player.wait_for_playback({'mirror': 'Rumble'}, '1', 1, None)
    messages = [command[1] for command in commands if command[0] == 'show-text']
    loading = [message for message in messages if 'Loading video…' in message or 'Taking longer' in message or 'Still loading' in message]
    if previously_played:
        assert any('Loading video… (0s)' in message for message in loading)
        assert any('Taking longer than usual… (10s)' in message for message in loading)
        assert all('Rumble' in message for message in loading if 'Press Enter' not in message)
        assert not any('Switching in' in message for message in messages)
        if outcome == 'ready':
            assert 20 <= len(loading) <= 25
            assert '' in messages
            assert messages[-1] == ''
        else:
            assert any('Press Enter' in message for message in messages)
            assert messages[-1] == ''
            assert clock[0] - started[0] >= 70
    else:
        assert loading == []
