"""Immutable, already-resolved episode choices projected into shared UI data."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any

from ..ui.help import picker_help
from ..ui.styled_text import StyledText, TextRun
from ..ui.view_model import ActionHint, Inherit, ViewAction, ViewItem, ViewSpec
from .details_menu_view import build_details_menu_view


@dataclass(frozen=True)
class EpisodeMenuOption:
    episode_id: str
    label: str
    watched: bool = False
    downloaded: bool = False
    enabled: bool = True

    def __post_init__(self) -> None:
        for name, value in (("episode_id", self.episode_id), ("label", self.label)):
            TextRun(value, "details")
            if not value.strip():
                raise ValueError(f"EpisodeMenuOption.{name} requires nonblank text")
        for name in ("watched", "downloaded", "enabled"):
            if not isinstance(getattr(self, name), bool):
                raise TypeError(f"EpisodeMenuOption.{name} requires a boolean")


def _label(option: EpisodeMenuOption) -> str | StyledText:
    if not option.watched and not option.downloaded:
        return option.label
    runs: tuple[TextRun, ...] = (TextRun(option.label, "muted" if option.watched else "episode.label"),)
    if option.downloaded:
        runs += (TextRun("  \u2714", "episode.downloaded"),)
    return StyledText(runs)


def _hint(option: EpisodeMenuOption, local_only: bool) -> StyledText:
    if option.downloaded:
        text = "watched \u2022 ready to watch" if option.watched else "ready to watch"
    elif option.watched:
        text = "watched \u2022 not downloaded" if local_only else "watched"
    elif local_only:
        return StyledText((TextRun("\u2718", "episode.missing"), TextRun(" not downloaded", "muted")))
    else:
        text = ""
    return StyledText((TextRun(text, "muted"),))


def build_episode_menu_view(
    show: dict[str, Any],
    options: Sequence[EpisodeMenuOption],
    *,
    view_id: str,
    image_key: str,
    ttype: str,
    context: tuple[str, ...] = (),
    local_only: bool = False,
    show_cover: bool = True,
    feedback: str = "",
) -> ViewSpec:
    """Caller owns ordering, episode labels and watched/download resolution."""
    if not isinstance(local_only, bool):
        raise TypeError("Episode menu local_only requires a boolean")
    options = tuple(options)
    if any(not isinstance(option, EpisodeMenuOption) for option in options):
        raise TypeError("Episode menu options require EpisodeMenuOption values")
    base = build_details_menu_view(
        show, (), view_id=view_id, image_key=image_key, ttype=ttype,
        context=context, local_only=local_only, show_cover=show_cover, feedback=feedback,
    )
    has_downloads = any(option.downloaded and option.enabled for option in options)
    items = tuple(
        ViewItem(
            f"episode:{ttype}:{option.episode_id}", _label(option), hint=_hint(option, local_only),
            details=Inherit, enabled=option.enabled, reference=option.episode_id,
            action_hints=(ActionHint("select", "Download" if local_only and not option.downloaded else "Play"),)
            + ((ActionHint("delete", "Delete", visible=option.downloaded),) if has_downloads else ()),
        )
        for option in options
    )
    actions = tuple(base.actions) + (ViewAction("flip", "flip", ("CTRL_R",)),)
    if has_downloads:
        actions = (actions[0], ViewAction("delete", "Delete episode", ("DELETE", "CTRL_D"), footer=False), *actions[1:])
    help_data = picker_help(
        "Play episode", "Go back", "Go back", tab_label="Toggle Sub/Dub",
        shift_tab_label="Toggle Sub/Dub", reverse_label="Flip order",
        delete_label="Delete episode" if has_downloads else None, info_label="Title information",
    )
    return replace(
        base, prompt="Select episode", items=items, state="ready" if items else "empty",
        actions=actions, help=tuple(help_data.items()),
    )
