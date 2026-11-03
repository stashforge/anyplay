import subprocess
from types import SimpleNamespace


def test_ipc_player_cannot_compete_for_tui_keyboard_input(monkeypatch):
    from media_cli.playback import mpv

    launches = []
    monkeypatch.setattr("shutil.which", lambda _: "/test/mpv")

    def launch(command, **kwargs):
        launches.append((command, kwargs))
        return SimpleNamespace(poll=lambda: None)

    monkeypatch.setattr(mpv, "cleanup_mpv_runtime", lambda path: None)
    monkeypatch.setattr(mpv, "create_mpv_runtime", lambda: ("runtime", "socket", "conf", "chapters", "lua"))
    monkeypatch.setattr(mpv.subprocess, "Popen", launch)
    monkeypatch.setattr(mpv.os.path, "exists", lambda path: True)
    monkeypatch.setattr(
        mpv.socket,
        "socket",
        lambda *a: SimpleNamespace(
            connect=lambda path: None, setblocking=lambda value: None, sendall=lambda data: None
        ),
    )
    player = mpv.MpvIpc()
    player.start()
    command, kwargs = launches[0]
    assert "--input-terminal=no" in command
    assert "--force-window=no" in command
    assert "--force-window=yes" not in command
    assert kwargs["stdin"] == subprocess.DEVNULL
