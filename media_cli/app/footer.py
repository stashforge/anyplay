"""Small helpers for context labels in TUI footers."""

from __future__ import annotations

from typing import Any


def mode_label(ui: Any, ms: Any) -> str:
    if getattr(ms, "_is_downloads", False) or getattr(ui, "search_prev_state", "") == "DOWNLOADS":
        return "Downloads"
    if (
        getattr(ui, "search_prev_state", "") == "HISTORY"
        or getattr(ui, "action_prev_state", "") == "HISTORY"
        or getattr(ui, "ep_prev_state", "") == "HISTORY"
    ):
        return "History"
    return ""


def join_footer_parts(*parts: str) -> str:
    return " • ".join(str(part).strip() for part in parts if str(part or "").strip())


def provider_label(show: dict | None) -> str:
    if not isinstance(show, dict):
        return ""
    if not (show.get("_provider") or show.get("provider")):
        return ""
    if show.get("_has_provider_link") is False:
        return ""
    return str(show.get("_provider_name") or (show.get("_provider") or show.get("provider") or "").title())


def context_prefix(
    show: dict | None,
    ui: Any,
    ms: Any,
    *,
    offline: bool = False,
    mode: str | None = None,
) -> str:
    offline_badge = "\033[38;2;225;85;85m[Offline]\033[0m" if offline else ""
    return join_footer_parts(mode or mode_label(ui, ms), offline_badge, provider_label(show))


def context_footer(
    show: dict | None,
    ui: Any,
    ms: Any,
    *parts: str,
    offline: bool = False,
    mode: str | None = None,
) -> str:
    return join_footer_parts(context_prefix(show, ui, ms, offline=offline, mode=mode), *parts)
