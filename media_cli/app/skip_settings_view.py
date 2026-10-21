"""Data-only skip settings snapshots for the shared picker."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any, Literal

from ..ui.help import picker_help
from ..ui.setting_control import ChoiceSetting, toggle_setting
from ..ui.view_model import ViewAction, ViewSpec
from .details_menu_view import DetailsMenuOption, build_details_menu_view


@dataclass(frozen=True)
class SkipSettingsState:
    aniskip_enabled: bool
    auto_skip_enabled: bool
    custom_status: Literal["OFF", "Session", "Title"]

    def __post_init__(self) -> None:
        if not isinstance(self.aniskip_enabled, bool) or not isinstance(self.auto_skip_enabled, bool):
            raise TypeError("Skip settings toggles require booleans")
        if self.custom_status not in ("OFF", "Session", "Title"):
            raise ValueError("Unknown custom skip status")


def skip_toggle_controls(
    state: SkipSettingsState, *, on_aniskip: Callable[[bool], object],
    on_auto_skip: Callable[[bool], object], on_custom: Callable[[bool], object],
) -> tuple[ChoiceSetting[bool], ...]:
    return (
        toggle_setting("action:TOGGLE_ANISKIP", "AniSkip", state.aniskip_enabled, on_aniskip,
                       off_label="OFF", on_label="ON", hint="global/configured AniSkip lookup"),
        toggle_setting("action:TOGGLE_AUTO_SKIP", "Auto-Skip", state.auto_skip_enabled, on_auto_skip,
                       off_label="OFF", on_label="ON", hint="auto-skip when skip intervals are available"),
        toggle_setting("action:TOGGLE_CUSTOM_SKIPS", "Custom Skips", state.custom_status != "OFF", on_custom,
                       off_label="OFF", on_label=state.custom_status if state.custom_status != "OFF" else "ON",
                       hint="custom skips override AniSkip when present"),
    )


def skip_settings_options(state: SkipSettingsState) -> tuple[DetailsMenuOption, ...]:
    return (
        DetailsMenuOption(
            "TOGGLE_ANISKIP", f"AniSkip: {'ON' if state.aniskip_enabled else 'OFF'}",
            "global/configured AniSkip lookup",
        ),
        DetailsMenuOption(
            "TOGGLE_AUTO_SKIP", f"Auto-Skip: {'ON' if state.auto_skip_enabled else 'OFF'}",
            "auto-skip when skip intervals are available",
        ),
        DetailsMenuOption(
            "TOGGLE_CUSTOM_SKIPS", f"Custom Skips: {state.custom_status}",
            "custom skips override AniSkip when present",
        ),
        DetailsMenuOption(
            "EDIT_CUSTOM_SKIPS", "Edit Custom Skips",
            "open editor for this title/provider skip form", opens_menu=True,
        ),
        DetailsMenuOption(
            "CLEAR_SESSION_SKIPS", "Clear Session Skips",
            "remove custom skips for this title until exit",
        ),
        DetailsMenuOption(
            "CLEAR_SAVED_SKIPS", "Clear Saved Title Skips",
            "remove persisted custom skips for this title",
        ),
    )


def build_skip_settings_view(
    show: dict[str, Any],
    state: SkipSettingsState,
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
    """Project caller-resolved settings without reading config, files or providers."""
    options = skip_settings_options(state)
    view = build_details_menu_view(
        show, options, view_id=view_id, image_key=image_key,
        ttype=ttype, context=context, feedback=feedback, show_cover=show_cover,
        local_only=local_only,
    )
    help_data = picker_help("Skip Settings", "Back", "Back", info_label="Title information")
    help_data["Left Arrow"] = "Previous value at filter start; otherwise move cursor or go back"
    help_data["Right Arrow"] = "Next value at filter end; otherwise move cursor or open"
    actions = tuple(action for action in view.actions if action.id != "translation") + (
        ViewAction("increase", "Increase", footer=False),
        ViewAction("decrease", "Decrease", footer=False),
    )
    controlled = {control.id: control.item() for control in controls}
    return replace(
        view,
        prompt="Skip Settings",
        items=tuple(replace(controlled[item.id], reference=item.reference) if item.id in controlled
                    else replace(item, adjustable=option.action_id.startswith("TOGGLE_"))
                    for item, option in zip(view.items, options, strict=True)),
        actions=actions,
        help=tuple(help_data.items()),
    )
