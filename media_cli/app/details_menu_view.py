"""Project prepared application menu choices into shared immutable UI data."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from ..core.terminal import sanitize_terminal_text
from ..ui.footer import Footer, FooterStatus, StatusKind
from ..ui.help import picker_help
from ..ui.poster import PosterManager
from ..ui.styled_text import TextRun
from ..ui.view_model import ImageSpec, Inherit, ViewAction, ViewItem, ViewSpec
from .show_view_data import build_show_detail_lines


@dataclass(frozen=True)
class DetailsMenuOption:
    action_id: str
    label: str
    hint: str = ""
    opens_menu: bool = False
    enabled: bool = True

    def __post_init__(self) -> None:
        for name, value in (("action_id", self.action_id), ("label", self.label), ("hint", self.hint)):
            TextRun(value, "details")
            if name != "hint" and not value.strip():
                raise ValueError(f"DetailsMenuOption.{name} requires a nonblank string")
        if not isinstance(self.opens_menu, bool):
            raise TypeError("DetailsMenuOption.opens_menu requires a boolean")
        if not isinstance(self.enabled, bool):
            raise TypeError("DetailsMenuOption.enabled requires a boolean")


def build_details_menu_view(
    show: dict[str, Any],
    options: Sequence[DetailsMenuOption],
    *,
    view_id: str,
    image_key: str,
    ttype: str,
    context: tuple[str, ...] = (),
    local_only: bool = False,
    feedback: str = "",
    show_cover: bool = True,
    refresh_enabled: bool = False,
    loading_message: str = '',
    loading_indicator: str = '',
) -> ViewSpec:
    """Callers supply identity, choices and context; no fetching or action decisions."""
    if not isinstance(show_cover, bool):
        raise TypeError("Details menu show_cover requires a boolean")
    if not isinstance(refresh_enabled, bool):
        raise TypeError("Details menu refresh_enabled requires a boolean")
    items = tuple(
        ViewItem(
            f"action:{option.action_id}",
            option.label,
            kind="submenu" if option.opens_menu else "item",
            hint=option.hint,
            details=Inherit,
            reference=option.action_id,
            enabled=option.enabled,
        )
        for option in options
    )
    url = PosterManager._get_cover_url(show) if show_cover else None
    image = (
        (ImageSpec(image_key, "available", url) if url else ImageSpec(image_key, "unavailable")) if show_cover else None
    )
    return ViewSpec(
        view_id,
        "Select action",
        items,
        details=build_show_detail_lines(show, ttype, local_only=local_only),
        image=image,
        context=tuple(sanitize_terminal_text(part) for part in context),
        state="ready" if items else "empty",
        footer=Footer(
            statuses=(FooterStatus(sanitize_terminal_text(feedback), StatusKind.WARNING),),
            include_cover_status=False,
        )
        if feedback
        else (Footer(statuses=(FooterStatus(loading_message, StatusKind.LOADING, indicator=loading_indicator),), include_cover_status=False)
              if loading_message else None),
        actions=(
            ViewAction("select", "select", ("ENTER", "RIGHT")),
            ViewAction("translation", "Sub/Dub", ("TAB",)),
            ViewAction("translation", "Sub/Dub", ("BTAB", "CTRL_N", "CTRL_P"), footer=False),
            ViewAction("help", "Help", ("?",)),
            ViewAction("back", "back", ("ESC", "LEFT")),
            ViewAction("information", "Information", ("CTRL_O",), footer=False),
        ) + ((ViewAction("refresh", "Refresh title", ("CTRL_R",), footer=False),) if refresh_enabled else ()),
        help=tuple(
            picker_help(
                "Select action",
                "Go back",
                "Go back",
                tab_label="Toggle Sub/Dub",
                info_label="Title information",
            ).items()
        ) + ((("Ctrl+R", "Refresh title"),) if refresh_enabled else ()),
    )
