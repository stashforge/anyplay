"""Render supplied view data while sharing the poster manager's mutable state."""

from __future__ import annotations

from dataclasses import replace
from itertools import count

from ..core.terminal import sanitize_terminal_text, truncate_display
from .actions import ActionAvailability, ActionBindings, ResolvedAction, key_label, normalize_key
from .background_loading import BackgroundLoadingStatus
from .detail_panel import render_detail_lines
from .footer import Footer, FooterStatus, StatusKind, render_footer
from .loading_model import LoadingInteraction, LoadingOwner, LoadingPlacement, LoadingTaskSpec
from .picker_render import loading_frame
from .tui_actions import tui_bindings
from .view_model import ResolvedDetails, ViewItem, resolve_details
from .view_session import ViewSession

# Match panels without importing its domain metadata and display dependencies.
C_TITLE = "\033[1;97m"
C_ALT = "\033[38;5;248m"
C_META = "\033[38;5;248m"
C_HINT = "\033[38;5;244m"
C_RESET = "\033[0m"
_SEPARATOR = " \u2022 "


class ViewPresentation:
    def __init__(self, session: ViewSession, poster_manager):
        self.session = session
        self.poster_manager = poster_manager
        self._shows = {}
        self._serial = count(1)
        self.loading = session.loading
        self._cover_loading: tuple[str, BackgroundLoadingStatus] | None = None
        self._view_loading: tuple[tuple[str, int], BackgroundLoadingStatus] | None = None

    def blocked_action(self, action: ResolvedAction) -> None:
        token = self.session.navigation_token
        if token is not None:
            self.loading.feedback(token, action)

    def selected(self, item_id=None) -> ViewItem | None:
        return self.session.item(item_id)

    def _details(self, item_id) -> ResolvedDetails:
        item = self.selected(item_id)
        return resolve_details(self.session.current.details, item.details if item is not None else None)

    def _image(self, item_id):
        item = self.selected(item_id)
        return item.image if item is not None and item.image is not None else self.session.current.image

    def image_enabled(self) -> bool:
        return bool(self.poster_manager.enabled())

    def show_for(self, item_id=None) -> dict:
        item = self.selected(item_id)
        view = self.session.current
        slot = (view.id, self.session.generation, item.id if item is not None else item_id)
        image = self._image(item_id)
        identity = (image.key, image.url, image.state) if image is not None else None
        existing = self._shows.get(slot)
        if existing is None or existing[0] != identity:
            # Never recycle an old dictionary, even if a previous URL returns:
            # a background worker may still hold and mutate that dictionary.
            show = {
                "_id": f"view:{view.id}:{self.session.generation}:{next(self._serial)}",
                "thumbnail": image.url or "" if image is not None else "",
                "_image_key": image.key if image is not None else None,
                "_image_state": image.state if image is not None else None,
            }
            if image is not None and image.state != "available":
                show["_poster_status"] = {"resolving": "resolving", "unavailable": "missing", "failed": "failed"}[image.state]
            self._shows[slot] = (identity, show)
        else:
            show = existing[1]
        show["name"] = sanitize_terminal_text(item.label if item is not None else self._details(item_id).title)
        show["_image_message"] = sanitize_terminal_text(image.message) if image is not None else ""
        return show

    @property
    def selected_show_id(self) -> str:
        return self.show_for()["_id"]

    def poster(self, item_id=None) -> str:
        show = self.show_for(item_id)
        if not self.image_enabled() or show["_image_state"] != "available":
            return ""
        return self.poster_manager.get(show) or ""

    def _image_status(self, show) -> str:
        if not self.image_enabled():
            return ""
        state = show["_image_state"]
        if state is None:
            return ""
        if state == "available":
            if show.get("_poster_raw"):
                return ""
            label = {
                "queued": "Loading cover", "downloading": "Loading cover",
                "rendering": "Loading cover", "loading": "Loading cover",
                "failed": "Cover failed", "missing": "Cover unavailable",
                "no_chafa": "Cover needs chafa",
            }.get(show.get("_poster_status"), "")
        else:
            label = {"resolving": "Fetching cover", "unavailable": "Cover unavailable", "failed": "Cover failed"}[state]
        message = show["_image_message"]
        return f"{label}: {message}" if label and message else label

    def _view_message(self) -> str:
        if self.session.navigation is not None:
            snapshot = self.session.loading_snapshot()
            return snapshot.message if snapshot is not None else ""
        view = self.session.current
        message = sanitize_terminal_text(view.message)
        if not message and not view.items:
            message = {
                "loading": "Loading...", "failed": "Unable to load results",
            }.get(view.state, "No results")
        return message

    def _cover_resolution_status(self, show, frame):
        resolving = self.image_enabled() and show["_image_state"] == "resolving"
        identity = show["_id"] if resolving else None
        if self._cover_loading is not None and self._cover_loading[0] != identity:
            self._cover_loading[1].read(False)
            self._cover_loading = None
        if not resolving:
            return None
        if self._cover_loading is None:
            spec = LoadingTaskSpec(
                "cover-resolution", LoadingOwner(identity, 0), "Fetching cover",
                LoadingInteraction.BACKGROUND, LoadingPlacement.FOOTER,
                ActionBindings(()), delay=0,
            )
            self._cover_loading = (identity, BackgroundLoadingStatus(spec, None, frame=frame))
        status = self._cover_loading[1].read(True)
        # Image diagnostic details retain their existing presentation contract.
        return replace(status, message=self._image_status(show)) if status is not None else None

    def _view_loading_status(self, frame):
        view = self.session.current
        message = self._view_message()
        active = (
            self.session.navigation is None and view.state == "loading"
            and bool(message)
        )
        identity = (view.id, self.session.generation) if active else None
        if self._view_loading is not None and self._view_loading[0] != identity:
            self._view_loading[1].read(False)
            self._view_loading = None
        if not active:
            return None
        if self._view_loading is None:
            spec = LoadingTaskSpec(
                "view-loading", LoadingOwner(view.id, self.session.generation), message,
                LoadingInteraction.BACKGROUND, LoadingPlacement.FOOTER,
                ActionBindings(()), delay=0,
            )
            self._view_loading = (identity, BackgroundLoadingStatus(spec, None, frame=frame))
        return self._view_loading[1].read(True, message=message)

    def status_text(self, item_id=None) -> str:
        """Readable current status; observing it never starts an image request."""
        return _SEPARATOR.join(part for part in (
            self._view_message(),
            self._image_status(self.show_for(item_id)),
        ) if part)

    def _context(self) -> str:
        return _SEPARATOR.join(text for part in self.session.current.context if (text := sanitize_terminal_text(part)))

    def _navigation(self) -> str:
        return _SEPARATOR.join(self._footer().context + self._footer().actions)

    def _footer(self, statuses=()) -> Footer:
        parts = []
        advertised = set()
        pending = self.session.navigation is not None
        bindings = tui_bindings(self.session.current, navigation_pending=pending)
        for action in self.session.current.actions:
            keys = []
            for key in action.keys:
                binding = bindings.resolve(key)
                canonical = normalize_key(key)
                if canonical in {"LEFT", "RIGHT"}:
                    continue
                if (
                    binding is not None and binding.availability is ActionAvailability.AVAILABLE
                    and binding.action.id == action.id and binding.action.label == action.label
                    and canonical not in advertised
                ):
                    keys.append(key_label(key))
                    advertised.add(canonical)
            if (action.keys and not keys) or (not action.keys and pending):
                continue
            label = sanitize_terminal_text(action.label)
            parts.append(f"{'/'.join(keys)}={label}" if keys else label)
        defaults = tuple(
            key_label(key) for key in ("ESC",)
            if key not in advertised and (binding := bindings.resolve(key)) is not None
            and binding.action.id == "back" and binding.availability is ActionAvailability.AVAILABLE
        )
        if defaults:
            parts.append(f"{'/'.join(defaults)}=back")
        return Footer(statuses=tuple(statuses),
                      context=tuple(text for part in self.session.current.context if (text := sanitize_terminal_text(part))),
                      actions=tuple(part for part in parts if part))

    @staticmethod
    def _line(text, color, width):
        bounded = truncate_display(text, max(1, int(width) - 1))
        return f"{color}{bounded}{C_RESET}"

    def details(self, item_id=None, width=80) -> str:
        fields = self._details(item_id)
        lines = list(render_detail_lines(
            primary=fields.title, secondary=fields.secondary, details=fields.metadata, width=width,
        ))
        show = self.show_for(item_id)
        message = self._view_message()
        if self.session.navigation is not None:
            snapshot = self.session.loading_snapshot(feedback=True)
            message = snapshot.message if snapshot is not None and snapshot.visible else ""
        frame = getattr(self.poster_manager, "loading_frame", loading_frame)
        cover_status = self._cover_resolution_status(show, frame)
        view_loading = self._view_loading_status(frame)
        busy = self.session.navigation is not None or self.session.current.state == "loading"
        kind = StatusKind.LOADING if busy else StatusKind.ERROR if self.session.current.state == "failed" else self.session.current.message_kind
        if view_loading is not None:
            statuses = [view_loading]
        else:
            statuses = [FooterStatus(message, kind, frame() if busy else "")] if message else []
        if self.image_enabled() and show["_image_state"] == "available":
            self.poster_manager.get(show)
            footer = self.poster_manager.footer_line(show, self._footer(statuses), width, persistent=True)
        else:
            status = self._image_status(show)
            if status:
                resolving = show["_image_state"] == "resolving"
                if resolving:
                    if cover_status is not None:
                        statuses.append(cover_status)
                else:
                    kind = StatusKind.ERROR if show["_image_state"] == "failed" else StatusKind.WARNING
                    statuses.append(FooterStatus(status, kind))
            footer = render_footer(self._footer(statuses), width)
        lines.append(footer)
        return "\n".join(lines)

    def plain_details(self, item_id=None) -> list[str]:
        fields = self._details(item_id)
        return [sanitize_terminal_text(text) for text in (fields.title, fields.secondary, fields.metadata)] + [
            _SEPARATOR.join(part for part in (self._context(), self.status_text(item_id)) if part)
        ]

    def info(self, item_id=None) -> str:
        """Use only explicitly supplied information, without enriching metadata."""
        item = self.selected(item_id)
        if item is not None and item.info:
            return "\n".join(sanitize_terminal_text(line) for line in item.info.split("\n"))
        return "\n".join(self.plain_details(item_id)[:3])

    def needs_tick(self) -> bool:
        if (self.session.navigation is not None or self.session.current.state == "loading") and self._view_message():
            return True
        if not self.image_enabled():
            return False
        show = self.show_for()
        if show["_image_state"] == "resolving":
            return True
        return show["_image_state"] == "available" and bool(self.poster_manager.needs_tick(show))
