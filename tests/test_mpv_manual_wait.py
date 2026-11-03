"""Slow playback stays user-controlled; explicit failures still recover."""

import json
from types import SimpleNamespace

import pytest

from media_cli.playback import mpv
from media_cli.ui.player_screen import _playback_status_text


@pytest.mark.parametrize('source', ['terminal', 'mpv'])
@pytest.mark.parametrize('mode', ['startup', 'buffering', 'paused', 'resumed', 'waiting', 'error'])
def test_wait_prompt_gates_enter_without_timeout_failure(monkeypatch, source, mode):
    clock = [0.0]
    events, commands, snapshots = [], [], []
    stdin_keys = []
    player = mpv.MpvIpc(redraw_callback=lambda props: snapshots.append({**props, '_test_clock': clock[0]}))
    player.process = SimpleNamespace(poll=lambda: None)
    player.running = True
    def recv(size):
        if not events:
            raise BlockingIOError
        return (json.dumps(events.pop(0))+'\n').encode()

    player.client = SimpleNamespace(recv=recv)
    stdin = SimpleNamespace(fileno=lambda: 0, read=lambda size: stdin_keys.pop(0))
    monkeypatch.setattr(mpv.sys, 'stdin', stdin)
    monkeypatch.setattr(mpv.os, 'isatty', lambda fd: False)
    monkeypatch.setattr(mpv.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(player, 'start', lambda: None)

    def send(*args):
        commands.append(args)
        if args == ('stop',):
            events.append({'event': 'end-file', 'reason': 'stop'})

    monkeypatch.setattr(player, 'send_cmd', send)
    fired = set()

    def key():
        if source == 'terminal':
            stdin_keys.append('\r')
        else:
            events.append({'event': 'client-message', 'args': ['next_mirror', str(player._load_serial)]})

    def select(*args):
        clock[0] += 1
        if mode not in ('startup', 'waiting') and 'loaded' not in fired:
            fired.add('loaded')
            events.extend([{'event': 'file-loaded'}, {'event': 'playback-restart'},
                           {'event': 'property-change', 'name': 'playback-time', 'data': 1}])
            if mode == 'paused':
                events.append({'event': 'property-change', 'name': 'pause', 'data': True})
        if clock[0] == 10:
            key()  # Never switches before the prompt.
        if mode == 'resumed' and clock[0] == 64:
            events.append({'event': 'property-change', 'name': 'playback-time', 'data': 2})
        if source == 'mpv' and clock[0] == 65:
            events.append({'event': 'client-message', 'args': ['next_mirror', 'retired-load']})
        if clock[0] == 66 and mode != 'waiting':
            key()
        if mode == 'error' and clock[0] == 20:
            events.append({'event': 'end-file', 'reason': 'error'})
        if clock[0] >= 70:
            events.append({'event': 'end-file', 'reason': 'quit'})
        return ([player.client] if events else []) + ([stdin] if stdin_keys else []), [], []

    monkeypatch.setattr(mpv.select, 'select', select)
    player.load('https://example.test/video', 'Title', {}, '')
    result, _ = player.wait_for_playback({}, '1', 1, None)
    expected = 'ERROR' if mode == 'error' else 'QUIT' if mode in ('paused', 'resumed', 'waiting') else 'NEXT_MIRROR'
    assert result == expected
    prompts = [s.get('_mirror_wait_message') for s in snapshots if s.get('_mirror_wait_message')]
    if mode in ('startup', 'buffering', 'resumed', 'waiting'):
        assert prompts
        assert len(set(prompts)) == 1
        assert all(s['_test_clock'] >= 60 for s in snapshots if s.get('_mirror_wait_message'))
        assert any(c[0] == 'show-text' and prompts[0] in c[1] for c in commands)
        assert 'Enter' in _playback_status_text({'_mirror_wait_message': prompts[0]})
        assert ('script-message', 'set_mirror_prompt', str(player._load_serial), 'no') in commands
    else:
        assert not prompts
    assert not player.props.get('_mirror_wait_message')
    assert not any('Switching in' in str(c) for c in commands)
