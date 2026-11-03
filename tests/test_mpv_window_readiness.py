"""Initial mpv startup stays windowless; existing playback windows stay useful."""

import json
from types import SimpleNamespace

import pytest

from media_cli.playback import mpv
from media_cli.cli.args import parse_cli_args


@pytest.mark.parametrize('command', ['search', 'history', 'downloads'])
@pytest.mark.parametrize('mode', ['ready', 'immediate'])
def test_window_flag_available_in_playback_commands(command, mode):
    args, _ = parse_cli_args([command, '--mpv-window', mode])
    assert args.mpv_window == mode


@pytest.mark.parametrize('mode,expected', [('ready', '--force-window=no'),
                                           ('immediate', '--force-window=yes')])
def test_window_mode_controls_mpv_launch(monkeypatch, mode, expected):
    from media_cli.context import FLAGS
    from media_cli.services import requirements
    monkeypatch.setattr(FLAGS, 'mpv_window', mode, raising=False)
    monkeypatch.setattr(requirements, 'require_executable', lambda *a: None)
    monkeypatch.setattr(mpv, 'create_mpv_runtime', lambda: ('dir', 'socket', 'conf', 'chapters', 'lua'))
    monkeypatch.setattr(mpv, 'cleanup_mpv_runtime', lambda *a: None)
    monkeypatch.setattr(mpv.os.path, 'exists', lambda *a: True)
    launches = []
    monkeypatch.setattr(mpv.subprocess, 'Popen',
                        lambda args, **kw: launches.append(args) or SimpleNamespace(poll=lambda: None))
    monkeypatch.setattr(mpv.socket, 'socket', lambda *a: SimpleNamespace(
        connect=lambda *a: None, setblocking=lambda *a: None))
    player = mpv.MpvIpc()
    monkeypatch.setattr(player, 'send_cmd', lambda *a: None)
    player.start()
    assert expected in launches[0]


@pytest.mark.parametrize('ready_event', [
    {'event': 'playback-restart'},
    {'event': 'property-change', 'name': 'playback-time', 'data': 0.5},
])
def test_window_is_enabled_at_first_ready_not_file_loaded(monkeypatch, ready_event):
    player, commands, events, observations = setup_player(monkeypatch)
    events.extend([{'event': 'file-loaded'}, ready_event,
                   {'event': 'playback-restart'}, {'event': 'end-file', 'reason': 'error'}])
    assert player.wait_for_playback({}, '1', 1, None)[0] == 'ERROR'
    assert observations[1] == []  # file-loaded alone must not force a window.
    window_commands = [command for command in commands if command[:2] == ('set_property', 'force-window')]
    assert window_commands == [('set_property', 'force-window', True)]

    # Mirror replacement keeps the existing window and enables switch OSD.
    commands.clear()
    player.load('https://example.com/fallback.m3u8', 'Fallback', {}, '')
    assert player._startup_osd_enabled
    events.extend([{'event': 'file-loaded'}, {'event': 'playback-restart'},
                   {'event': 'end-file', 'reason': 'error'}])
    player.wait_for_playback({}, '1', 1, None)
    assert not [command for command in commands if command[:2] == ('set_property', 'force-window')]


def test_initial_failure_never_forces_an_empty_window(monkeypatch):
    player, commands, events, observations = setup_player(monkeypatch)
    events.append({'event': 'end-file', 'reason': 'error'})
    assert player.wait_for_playback({}, '1', 1, None)[0] == 'ERROR'
    assert not [command for command in commands if command[:2] == ('set_property', 'force-window')]
    assert not player._played_in_process


def test_playback_owner_polls_and_attaches_ready_subtitles(monkeypatch):
    player, commands, events, _ = setup_player(monkeypatch)
    events.extend([{'event': 'file-loaded'}, {'event': 'playback-restart'},
                   {'event': 'end-file', 'reason': 'error'}])
    delivered = []

    def poll():
        if not delivered:
            delivered.append(True)
            player.queue_subtitles([{'url': '/tmp/prepared.vtt'}], load_serial=player._load_serial)

    player.wait_for_playback({}, '1', 1, None, poll_subtitles=poll)
    assert [c for c in commands if c[0] == 'sub-add'] == [
        ('sub-add', '/tmp/prepared.vtt', 'select', 'Subtitle')]


def setup_player(monkeypatch):
    commands = []
    events = []
    observations = []
    player = mpv.MpvIpc()
    player.process = SimpleNamespace(poll=lambda: None)
    player.running = True

    def recv(size):
        if not events:
            raise BlockingIOError
        return (json.dumps(events.pop(0))+'\n').encode()

    def select(*args):
        observations.append([command for command in commands if command[:2] == ('set_property', 'force-window')])
        return ([player.client] if events else [], [], [])

    player.client = SimpleNamespace(recv=recv)
    monkeypatch.setattr(player, 'start', lambda: None)
    monkeypatch.setattr(player, 'send_cmd', lambda *args: commands.append(args))
    monkeypatch.setattr(mpv.sys, 'stdin', SimpleNamespace(fileno=lambda: 0))
    monkeypatch.setattr(mpv.os, 'isatty', lambda fd: False)
    monkeypatch.setattr(mpv.select, 'select', select)
    player.load('https://example.com/first.m3u8', 'First', {}, '')
    return player, commands, events, observations
