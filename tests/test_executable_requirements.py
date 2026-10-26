"""Playback tools are needed at launch, not while browsing."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from media_cli.core.api import ProviderDependencyError


def test_missing_tool_has_an_actionable_typed_error(monkeypatch):
    from media_cli.services.requirements import require_executable

    monkeypatch.setattr("shutil.which", lambda _: None)
    with pytest.raises(ProviderDependencyError, match="mpv") as caught:
        require_executable("mpv", "Desktop playback")
    assert "Install" in str(caught.value)
    assert "PATH" in str(caught.value)
    assert "pip install" not in str(caught.value)


def test_missing_mpv_does_not_create_runtime_or_launch(monkeypatch):
    from media_cli.playback import mpv

    monkeypatch.setattr("shutil.which", lambda _: None)
    create = Mock(side_effect=AssertionError("Missing player must not create files"))
    monkeypatch.setattr(mpv, "create_mpv_runtime", create)
    with pytest.raises(ProviderDependencyError, match="mpv"):
        mpv.MpvIpc().start()
    create.assert_not_called()


def test_running_mpv_does_not_probe_again(monkeypatch):
    from media_cli.playback.mpv import MpvIpc

    monkeypatch.setattr("shutil.which", Mock(side_effect=AssertionError("Running player needs no tool lookup")))
    ipc = MpvIpc()
    ipc.process = SimpleNamespace(poll=lambda: None)
    ipc.client = object()
    ipc.start()
    assert ipc.running
    ipc.process = ipc.client = None


@pytest.mark.parametrize("termux,player,tool", [(False, "mpv", "mpv"), (False, "vlc", "vlc"), (True, "mpv", "am")])
def test_local_playback_checks_only_selected_tool(monkeypatch, tmp_path, termux, player, tool):
    from media_cli.playback import local

    path = tmp_path / "video.mkv"
    path.touch()
    checked, launched = [], []
    monkeypatch.setattr("shutil.which", lambda name: checked.append(name) or f"/bin/{name}")
    monkeypatch.setattr(local.subprocess, "run", lambda command, **kw: launched.append(command))
    assert local.play_local_video(str(path), player, termux=termux, error=pytest.fail)
    assert checked == [tool]
    assert launched[0][0] == tool


@pytest.mark.parametrize("termux", [False, True])
def test_missing_local_player_raises_before_launch(monkeypatch, tmp_path, termux):
    from media_cli.playback import local

    path = tmp_path / "video.mkv"
    path.touch()
    monkeypatch.setattr("shutil.which", lambda _: None)
    monkeypatch.setattr(local.subprocess, "run", Mock(side_effect=AssertionError("Must not launch")))
    with pytest.raises(ProviderDependencyError, match="am" if termux else "vlc"):
        local.play_local_video(str(path), "vlc", termux=termux, error=pytest.fail)


def test_android_requires_am_before_preparing_stream(monkeypatch):
    from media_cli.playback import android

    monkeypatch.setattr("shutil.which", lambda _: None)
    prepare = Mock(side_effect=AssertionError("Missing am must not prepare subtitles"))
    with pytest.raises(ProviderDependencyError, match="am"):
        android.play_android("Title", "1", {"link": "https://example.test/video.mp4", "subtitles": [{"url": "https://example.test/sub.vtt"}]},
                             None, prepare_subtitles_fn=prepare)
    prepare.assert_not_called()


def test_android_ui_launcher_checks_its_actual_executable(monkeypatch):
    from media_cli.playback import engine

    monkeypatch.setattr("shutil.which", lambda _: None)
    dispatch = Mock(side_effect=AssertionError("Missing launcher must not dispatch"))
    monkeypatch.setattr(engine.subprocess, "Popen", dispatch)
    with pytest.raises(ProviderDependencyError, match="am"):
        engine._launch_android_player(["am", "start"])
    dispatch.assert_not_called()


def test_android_adapter_dependency_error_reaches_owner_and_cleans_proxy(monkeypatch):
    from media_cli.playback import android
    from media_cli.services.requirements import MissingExecutable

    closed = []
    error = MissingExecutable("am", "Android playback")
    monkeypatch.setattr(android, "start_local_proxy", lambda *a, **kw: ("http://127.0.0.1/video", object()))
    monkeypatch.setattr(android, "replace_active_local_proxy", lambda *a: None)
    monkeypatch.setattr(android, "cleanup_active_local_proxy", lambda: closed.append(True))
    with pytest.raises(ProviderDependencyError) as caught:
        android.play_android("Title", "1", {"link": "https://example.test/video.mp4", "requires_proxy": True},
                             None, player="vlc", aniskip_enabled=False,
                             launch_command_fn=Mock(side_effect=error))
    assert caught.value is error
    assert closed  # Includes clearing the previous active proxy.


def test_cli_missing_player_cleans_up_and_prints_install_error(monkeypatch, capsys):
    from media_cli.cli import application
    from media_cli.playback.mpv import MpvIpc

    events = []
    monkeypatch.setenv("ALLMANGA_NO_OS_EXIT", "1")
    monkeypatch.setattr(application.sys, "argv", ["anyplay", "--incognito"])
    monkeypatch.setattr("shutil.which", lambda _: None)
    monkeypatch.setattr(application.app, "app_main", lambda: MpvIpc().start())
    monkeypatch.setattr(application, "_cleanup", lambda: events.append("cleanup"))
    monkeypatch.setattr(application.app, "restore_terminal", lambda: events.append("restore"))
    monkeypatch.setattr(application.app, "write_exception_log", Mock(side_effect=AssertionError("Expected tool error is not a crash")))
    assert application.run() == 1
    output = capsys.readouterr()
    assert "requires 'mpv'" in output.out + output.err
    assert "Install mpv" in output.out + output.err
    assert "unexpected error" not in (output.out + output.err).lower()
    assert events == ["cleanup", "restore"]
