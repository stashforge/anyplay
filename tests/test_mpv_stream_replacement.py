"""Stream replacement must not turn the previous file's end into a quit."""

import json
from collections import deque
from types import SimpleNamespace

import pytest

from media_cli.playback import mpv


def event(event_name, **fields):
    return {'event': event_name, **fields}


@pytest.fixture
def playback(monkeypatch):
    incoming = deque()
    commands = []
    clock = [0.0]

    def recv(size):
        if incoming:
            return incoming.popleft()
        raise BlockingIOError

    def tick():
        clock[0] += 0.1
        return clock[0]

    player = mpv.MpvIpc()
    player.running = True
    player.client = SimpleNamespace(recv=recv)
    player.process = SimpleNamespace(poll=lambda: None)
    monkeypatch.setattr(player, 'start', lambda: None)
    monkeypatch.setattr(player, 'send_cmd', lambda *args: commands.append(args))
    monkeypatch.setattr(mpv.sys, 'stdin', SimpleNamespace(fileno=lambda: 0, read=lambda n: 'q'))
    monkeypatch.setattr(mpv.os, 'isatty', lambda fd: False)
    monkeypatch.setattr(mpv.time, 'monotonic', tick)
    monkeypatch.setattr(mpv.select, 'select', lambda *args: ([player.client] if incoming else [], [], []))

    def feed(*events):
        incoming.append((''.join(json.dumps(msg) + '\n' for msg in events)).encode())

    def load():
        player.load('https://example.test/video', 'Example', {}, '')

    def wait():
        previous_select = mpv.select.select
        cutoff = clock[0] + getattr(player, 'disconnect_after', float('inf'))

        def select_with_disconnect(*args):
            if getattr(player, 'disconnect_after', 99) < 1 and clock[0] >= cutoff:
                incoming.append(b'')
            return previous_select(*args)

        monkeypatch.setattr(mpv.select, 'select', select_with_disconnect)
        try:
            return player.wait_for_playback({}, '1', 1, None)[0]
        finally:
            monkeypatch.setattr(mpv.select, 'select', previous_select)

    return SimpleNamespace(player=player, feed=feed, load=load, wait=wait, incoming=incoming, commands=commands)


def fail_first_stream(playback):
    playback.load()
    playback.player.disconnect_after = 0.8
    playback.feed(event('start-file', playlist_entry_id=11))
    assert playback.wait() == 'ERROR'
    playback.player.disconnect_after = 15


@pytest.mark.parametrize('reason,binge,closes', [
    ('eof', False, True), ('eof', True, False),
    ('error', False, False), ('quit', False, True),
])
def test_finished_window_closes_before_caller_completion_work(playback, reason, binge, closes):
    playback.load()
    playback.feed(event('start-file', playlist_entry_id=1), event('file-loaded'),
                  event('property-change', name='duration', data=100),
                  event('property-change', name='playback-time', data=0 if reason == 'error' else 100),
                  event('end-file', playlist_entry_id=1, reason=reason))
    result, _ = playback.player.wait_for_playback({}, '1', 1, None, is_binge=binge)
    assert result == {'eof': 'EOF', 'error': 'ERROR', 'quit': 'QUIT'}[reason]
    assert (('quit',) in playback.commands) == closes


@pytest.mark.parametrize('old_end_first', [True, False])
def test_fallback_survives_replaced_stream_stop(playback, old_end_first):
    fail_first_stream(playback)
    playback.load()
    old_end = event('end-file', playlist_entry_id=11, reason='stop')
    new_start = event('start-file', playlist_entry_id=12)
    playback.feed(*([old_end, new_start] if old_end_first else [new_start, old_end]),
                  event('file-loaded'), event('property-change', name='duration', data=100),
                  event('property-change', name='playback-time', data=100),
                  event('end-file', playlist_entry_id=12, reason='eof'))
    assert playback.wait() == 'EOF'
    assert playback.player.running


@pytest.mark.parametrize('reason,expected', [('error', 'ERROR'), ('stop', 'QUIT'), ('quit', 'QUIT')])
def test_new_stream_end_is_never_swallowed(playback, reason, expected):
    fail_first_stream(playback)
    playback.load()
    playback.feed(event('start-file', playlist_entry_id=12),
                  event('end-file', playlist_entry_id=12, reason=reason, error='loading failed'))
    assert playback.wait() == expected


def test_rapid_successive_fallbacks_preserve_each_failure(playback):
    fail_first_stream(playback)
    for old_id, new_id in [(11, 12), (12, 13)]:
        playback.load()
        playback.player.disconnect_after = 0.8
        playback.feed(event('end-file', playlist_entry_id=old_id, reason='stop'),
                      event('start-file', playlist_entry_id=new_id))
        assert playback.wait() == 'ERROR'
    playback.load()
    playback.feed(event('end-file', playlist_entry_id=13, reason='stop'),
                  event('start-file', playlist_entry_id=14),
                  event('end-file', playlist_entry_id=14, reason='error'))
    assert playback.wait() == 'ERROR'


def test_quit_during_replacement_is_honored(playback, monkeypatch):
    fail_first_stream(playback)
    playback.load()
    playback.feed(event('end-file', playlist_entry_id=11, reason='stop'))
    monkeypatch.setattr(mpv.select, 'select', lambda *args: ([mpv.sys.stdin, playback.player.client], [], []))
    assert playback.wait() == 'QUIT'
    assert ('stop',) in playback.commands


def test_queued_start_before_replacement_is_not_lost(playback):
    playback.load()
    # A start event can still be unread when another load drains the socket.
    playback.feed(event('start-file', playlist_entry_id=11))
    playback.load()
    playback.feed(event('end-file', playlist_entry_id=11, reason='stop'),
                  event('start-file', playlist_entry_id=12),
                  event('end-file', playlist_entry_id=12, reason='error'))
    assert playback.wait() == 'ERROR'


def test_unidentified_end_is_not_ignored_during_replacement(playback):
    fail_first_stream(playback)
    playback.load()
    playback.feed(event('end-file', reason='stop'))
    assert playback.wait() == 'QUIT'


def test_unrelated_entry_end_is_not_ignored(playback):
    fail_first_stream(playback)
    playback.load()
    playback.feed(event('end-file', playlist_entry_id=99, reason='stop'))
    assert playback.wait() == 'QUIT'


def test_partial_start_event_survives_disconnect_and_load_drain(playback):
    playback.load()
    playback.player.disconnect_after = 0.8
    raw = json.dumps(event('start-file', playlist_entry_id=11)).encode() + b'\n'
    playback.incoming.append(raw[:20])
    assert playback.wait() == 'ERROR'
    playback.incoming.append(raw[20:])
    playback.load()
    playback.player.disconnect_after = 15
    playback.feed(event('end-file', playlist_entry_id=11, reason='stop'),
                  event('start-file', playlist_entry_id=12),
                  event('end-file', playlist_entry_id=12, reason='error'))
    assert playback.wait() == 'ERROR'


@pytest.mark.parametrize('new_ready', ['file-loaded', 'playback-restart', 'position'])
def test_late_old_events_cannot_consume_replacement_audio_or_progress(playback, monkeypatch, caplog, new_ready):
    import logging

    fail_first_stream(playback)
    player = playback.player
    player.load('https://example.test/replacement', 'Replacement', {}, '',
                audio_url='https://example.test/audio', subtitle_url='/tmp/subtitle.vtt')
    attached = []
    original = player.send_cmd

    def send(*args):
        if args[0] in ('audio-add', 'sub-add'):
            attached.append((args[0], player._active_entry_id))
        original(*args)

    monkeypatch.setattr(player, 'send_cmd', send)
    ready_events = [event(new_ready)] if new_ready != 'position' else []
    playback.feed(event('file-loaded'), event('playback-restart'),
                  event('property-change', name='playback-time', data=999),
                  event('property-change', name='duration', data=9999),
                  event('end-file', playlist_entry_id=11, reason='stop'),
                  event('start-file', playlist_entry_id=12), *ready_events,
                  event('property-change', name='duration', data=100),
                  event('property-change', name='playback-time', data=5),
                  event('end-file', playlist_entry_id=12, reason='quit'))
    with caplog.at_level(logging.DEBUG, logger=mpv.__name__):
        assert playback.wait() == 'QUIT'
    assert attached == [('audio-add', 12), ('sub-add', 12)]
    assert player.props['playback-time'] == 5
    assert 'Ignoring retired mpv notification' in caplog.text
    assert 'mpv external tracks:' in caplog.text


def test_replacement_gets_loading_budget_after_its_actual_start(playback, monkeypatch):
    fail_first_stream(playback)
    playback.load()
    player = playback.player
    player.disconnect_after = 3
    began = [None]
    emitted = set()

    def select(*args):
        if began[0] is None:
            began[0] = mpv.time.monotonic()
        elapsed = mpv.time.monotonic() - began[0]
        if elapsed >= 2 and 'start' not in emitted:
            emitted.add('start')
            playback.feed(event('end-file', playlist_entry_id=11, reason='stop'),
                          event('start-file', playlist_entry_id=12))
        if elapsed >= 4 and 'loaded' not in emitted:
            emitted.add('loaded')
            playback.feed(event('file-loaded'), event('playback-restart'),
                          event('end-file', playlist_entry_id=12, reason='quit'))
        return ([player.client] if playback.incoming else [], [], [])

    monkeypatch.setattr(mpv.select, 'select', select)
    assert playback.wait() == 'QUIT'
    assert 'loaded' in emitted


@pytest.mark.parametrize("source", ["terminal", "mpv"])
@pytest.mark.parametrize("key", ["m", "v"])
def test_playback_menu_shortcuts_keep_player_alive(playback, monkeypatch, source, key):
    seen = []
    chosen = {"link": "https://example.test/selected"}
    playback.load()
    playback.player.playback_menu = lambda kind: (seen.append(kind) or "SELECT", chosen)
    if source == "mpv":
        playback.feed(event("client-message", args=["playback_mirrors" if key == "m" else "playback_versions"]))
    else:
        monkeypatch.setattr(mpv.sys.stdin, "read", lambda size: key)
        monkeypatch.setattr(mpv.select, "select", lambda *a: ([mpv.sys.stdin], [], []))
    assert playback.wait() == "MENU_SELECTION"
    assert seen == [key]
    assert playback.player.menu_selection is chosen
    assert ("stop",) not in playback.commands
    assert ("quit",) not in playback.commands
