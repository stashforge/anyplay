"""One session-scoped fullscreen control for desktop mpv playback."""

from ..ui.setting_control import toggle_setting


def apply_fullscreen(player):
    value = getattr(player, "fullscreen_override", None)
    if value is not None:
        player.send_cmd("set_property", "fullscreen", value)


def fullscreen_control(player, identity):
    override = getattr(player, "fullscreen_override", None)
    value = bool(override if override is not None else player.props.get("fullscreen", False))

    def changed(enabled):
        player.fullscreen_override = enabled
        apply_fullscreen(player)

    return toggle_setting(
        identity, "Fullscreen", value, changed,
        off_label="mpv config" if override is None and not value else "Off",
        on_label="mpv config" if override is None and value else "On",
        hint="desktop mpv; session only",
    )
