"""Data-only playback settings choices and view preparation."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import Any

from ..ui.help import picker_help
from ..ui.setting_control import ChoiceSetting, toggle_setting
from ..ui.view_model import ViewAction, ViewSpec
from .details_menu_view import DetailsMenuOption, build_details_menu_view


def playback_toggle_controls(
    cfg: dict[str, Any], ms: Any, *, on_download: Callable[[bool], object], on_delete: Callable[[bool], object],
) -> tuple[ChoiceSetting[bool], ...]:
    from .playback_player import auto_download_mode

    return (
        toggle_setting("action:TOGGLE_AUTO_DOWNLOAD", "Auto-Download Next",
                       auto_download_mode(cfg, getattr(ms, "auto_download_next", None)) == "adaptive",
                       on_download, on_label="Adaptive", hint="session only; download next when playback is safe"),
        toggle_setting("action:TOGGLE_AUTO_DELETE", "Auto-Delete Watched",
                       bool(getattr(ms, "auto_delete_watched", False)), on_delete,
                       off_label="OFF", on_label="ON", hint="session only; delete older watched downloads"),
    )


def playback_settings_options(cfg: dict[str, Any], ms: Any) -> tuple[DetailsMenuOption, ...]:
    """Return a snapshot of the playback settings currently shown by the menu."""
    from .playback_player import auto_download_mode

    dl_mode = auto_download_mode(cfg, getattr(ms, "auto_download_next", None))
    dl_status = "Adaptive" if dl_mode == "adaptive" else "Off"
    del_status = "ON" if getattr(ms, "auto_delete_watched", False) else "OFF"
    buffer = getattr(ms, "auto_delete_buffer", cfg.get("auto_delete_buffer", 1))
    buffer_label = f"Auto-Delete Buffer: {buffer} ep" + ("" if buffer == 1 else "s")
    return (
        DetailsMenuOption(
            "TOGGLE_AUTO_DOWNLOAD",
            f"Auto-Download Next: {dl_status}",
            "session only; download next when playback is safe",
        ),
        DetailsMenuOption(
            "TOGGLE_AUTO_DELETE",
            f"Auto-Delete Watched: {del_status}",
            "session only; delete older watched downloads",
        ),
        DetailsMenuOption(
            "CYCLE_AUTO_DELETE_BUFFER",
            buffer_label,
            "session only; keep recent watched downloads",
        ),
        DetailsMenuOption(
            "SKIP_SETTINGS",
            "Skip Settings",
            "AniSkip and custom opening/ending skips",
            opens_menu=True,
        ),
    )


def build_playback_settings_view(
    show: dict[str, Any],
    cfg: dict[str, Any],
    ms: Any,
    *,
    view_id: str,
    image_key: str,
    ttype: str,
    context: tuple[str, ...] = (),
    show_cover: bool = True,
    feedback: str = "",
    local_only: bool = False,
    controls: tuple[ChoiceSetting[bool], ...] = (),
) -> ViewSpec:
    """Build playback settings presentation data without preparing or mutating input."""
    view = build_details_menu_view(
        show,
        playback_settings_options(cfg, ms),
        view_id=view_id,
        image_key=image_key,
        ttype=ttype,
        context=context,
        feedback=feedback,
        show_cover=show_cover,
        local_only=local_only,
    )
    help_data = picker_help(
        "Playback",
        "Back",
        "Back",
        info_label="Title information",
    )
    help_data["Left Arrow"] = "Previous value at filter start; otherwise move cursor or go back"
    help_data["Right Arrow"] = "Next value at filter end; otherwise move cursor or open"
    actions = tuple(action for action in view.actions if action.id != "translation") + (
        ViewAction("increase", "Increase", footer=False),
        ViewAction("decrease", "Decrease", footer=False),
    )
    controlled = {control.id: control.item() for control in controls}
    items = tuple(replace(controlled[item.id], reference=item.reference) if item.id in controlled
                  else replace(item, adjustable=item.kind != "submenu") for item in view.items)
    existing_ids = {item.id for item in items}
    items += tuple(replace(control.item(), reference=control.id.removeprefix("action:"))
                   for control in controls if control.id not in existing_ids)
    return replace(
        view,
        prompt="Playback",
        actions=actions,
        items=items,
        help=tuple(help_data.items()),
    )
