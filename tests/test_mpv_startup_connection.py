from types import SimpleNamespace
import pytest
from media_cli.playback import mpv, desktop


def setup(monkeypatch, *, ready_after=3.0, exited=None, refused_once=False):
    clock = [0.0]
    attempts = []
    cleaned = []
    monkeypatch.setattr('shutil.which', lambda name: '/test/mpv')
    monkeypatch.setattr(mpv, 'create_mpv_runtime', lambda: ('runtime','socket','conf','chapters','lua'))
    monkeypatch.setattr(mpv, 'cleanup_mpv_runtime', cleaned.append)
    monkeypatch.setattr(mpv.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(mpv.time, 'sleep', lambda seconds: clock.__setitem__(0,clock[0]+seconds))
    monkeypatch.setattr(mpv.os.path, 'exists', lambda path: clock[0]>=ready_after)
    monkeypatch.setattr(mpv.subprocess, 'Popen', lambda *a, **kw: SimpleNamespace(poll=lambda: exited, wait=lambda **kw: None))
    def connect(path):
        attempts.append(clock[0])
        if refused_once and len(attempts)==1:
            raise ConnectionRefusedError()
    monkeypatch.setattr(mpv.socket, 'socket', lambda *a: SimpleNamespace(connect=connect,
        setblocking=lambda value:None,sendall=lambda data:None,close=lambda:None))
    return clock, attempts, cleaned


def test_slow_startup_waits_for_actual_socket(monkeypatch):
    clock, attempts, _ = setup(monkeypatch, ready_after=3.0)
    player=mpv.MpvIpc()
    player.start()
    assert player.running and clock[0]>=3.0
    assert len(attempts)==1


def test_socket_not_accepting_yet_is_retried(monkeypatch):
    _, attempts, _ = setup(monkeypatch, ready_after=0,refused_once=True)
    player=mpv.MpvIpc()
    player.start()
    assert player.running and len(attempts)==2


def test_process_exit_reports_typed_error_without_socket_attempt(monkeypatch):
    _, attempts, cleaned=setup(monkeypatch, exited=2)
    player=mpv.MpvIpc()
    with pytest.raises(mpv.MpvStartupError,match='code 2'):
        player.start()
    assert not attempts and player.process is None
    assert 'runtime' in cleaned


def test_missing_socket_has_bounded_wait_and_cleans_up(monkeypatch):
    clock, attempts, cleaned=setup(monkeypatch,ready_after=100)
    player=mpv.MpvIpc()
    with pytest.raises(mpv.MpvStartupError,match='15 seconds'):
        player.start()
    assert 15<=clock[0]<15.2 and not attempts
    assert player.process is None and 'runtime' in cleaned


def test_desktop_returns_startup_failure_instead_of_crashing(monkeypatch):
    player=SimpleNamespace(props={})
    def fail(player):
        raise mpv.MpvStartupError('mpv exited during startup (code 2).')
    result=desktop.play_desktop(player,'Title','1',{'link':'/tmp/test.mkv','type':'local'},
        get_resume_time=lambda *a:0,get_preferred_mirror=lambda *a:{},
        start_player_fn=fail,update_stream_info=lambda *a:None)
    assert result[0]=='STARTUP_FAILED'
    assert 'code 2' in player.startup_error
