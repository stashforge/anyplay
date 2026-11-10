from types import SimpleNamespace

from media_cli.app.fullscreen_setting import fullscreen_control, apply_fullscreen


def test_fullscreen_cycles_and_updates_running_player():
    calls = []
    player = SimpleNamespace(fullscreen_override=None, props={"fullscreen": False},
                             send_cmd=lambda *args: calls.append(args))
    control = fullscreen_control(player, "fullscreen")
    control.change()
    assert player.fullscreen_override is True
    assert calls == [("set_property", "fullscreen", True)]


def test_default_does_not_override_mpv_configuration():
    calls = []
    player = SimpleNamespace(fullscreen_override=None, send_cmd=lambda *args: calls.append(args))
    apply_fullscreen(player)
    assert not calls
