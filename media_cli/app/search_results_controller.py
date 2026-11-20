"""Normal-search workflow supplies views and consumes semantic UI events."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Literal

from ..state.theme import APPLICATION_THEME
from ..ui.footer import FooterStatus
from ..ui.poster import PosterManager
from ..ui.view_picker import run_view_session
from ..ui.view_session import ExitSession, ReplaceView, ViewSession
from .search_results_view import build_search_results_view


@dataclass(frozen=True)
class SearchResultsResult:
    kind: Literal["selected", "back", "quit", "empty"]
    show: dict | None = None
    error: str = ""

    def __post_init__(self):
        if self.kind not in {"selected", "back", "quit", "empty"}:
            raise ValueError("Unknown search result outcome")
        if self.kind == "selected" and not isinstance(self.show, dict):
            raise TypeError("Selected outcome requires its provider record")
        if self.kind != "selected" and self.show is not None:
            raise ValueError("Only selection may carry a provider record")


class SearchResultsController:
    def __init__(
        self,
        live_fn,
        get_results,
        get_loading,
        get_error,
        *,
        ui,
        provider_id,
        provider_name,
        query,
        ttype,
        prompt,
        auto_single,
        select,
        information,
        badges=(),
        clock=time.monotonic,
    ):
        self._live = live_fn
        self._get_results = get_results
        self._get_loading = get_loading
        self._get_error = get_error
        self._ui = ui
        self._select = select
        self._information = information
        self._clock = clock
        self._next_poll = 0.0
        self._done = False
        self._options = dict(
            provider_id=provider_id,
            provider_name=provider_name,
            query=query,
            ttype=ttype,
            prompt=prompt,
            auto_single=auto_single,
            badges=tuple(badges),
        )
        self._published = self._read("")
        self.session = ViewSession(self._published, self._handle_event)

    def _read(self, filter_query):
        # Existing sources own asynchronous fetching and plain-mode completion.
        # Their legacy labels/formatted header are discarded at this boundary.
        self._done = bool(self._live(filter_query)[2]) if self._live is not None else True
        status = self._get_loading()
        message = status.message if isinstance(status, FooterStatus) else str(status or "")
        return build_search_results_view(
            tuple(self._get_results()),
            **self._options,
            loading=not self._done,
            message=message,
            error=self._get_error() or self._ui.search_error,
        )

    def updates(self, *, force=False):
        """Read ready data on the UI owner; never wait for provider work here."""
        if self.session.exited:
            return ()
        now = self._clock()
        if not force and now < self._next_poll:
            return ()
        self._next_poll = now + 0.1
        view = self._read(self.session.position.query)
        commands = []
        if view != self._published:
            self._published = view
            commands.append(ReplaceView(self.session.current.id, self.session.generation, view))
        if self._done and not view.items:
            commands.append(ExitSession(SearchResultsResult("empty", error=view.message)))
        return tuple(commands)

    def _retain_cover(self, item):
        visible = self._ui.hovered_show_obj
        show = item.reference
        if (
            isinstance(visible, dict)
            and visible.get("_image_key") == item.id
            and PosterManager._get_cover_url(visible) == PosterManager._get_cover_url(show)
            and visible.get("_poster_status") == "ready"
            and visible.get("_poster_raw")
        ):
            show.update(
                _poster_raw=visible["_poster_raw"],
                _poster_status="ready",
                _poster_status_time=visible.get("_poster_status_time", time.time()),
            )
            show.pop("_poster_failed", None)

    def _handle_event(self, event):
        if event.kind in {"back", "quit"}:
            return ExitSession(SearchResultsResult(event.kind))
        item = self.session.item(event.item_id)
        if item is None:
            return None
        if event.kind == "select":
            self._retain_cover(item)
            if self._select(item.reference):
                return ExitSession(SearchResultsResult("selected", item.reference))
            return self.updates(force=True)
        if event.kind == "action" and event.value == "information":
            self._retain_cover(item)
            self._information(item.reference)
            return self.updates(force=True)
        return None


def run_search_results(flags, ui, *, restore_position=True, presentation=None, **options) -> SearchResultsResult:
    try:
        controller = SearchResultsController(ui=ui, **options)
        picker = None
        theme = APPLICATION_THEME
        if presentation is not None and not flags.plain_mode:
            from ..state.theme import MINT_SCREEN_THEME
            from ..ui.action_picker import action_picker
            from ..ui.library_info import library_info_lines
            from ..ui.footer import Footer, FooterStatus, StatusKind, render_footer
            from ..ui.picker_render import loading_frame
            theme = MINT_SCREEN_THEME

            def information(index, width):
                items = controller.session.current.items
                return library_info_lines(items[index].reference, options["ttype"], width, local_only=False,
                                          context=options["provider_name"]) if 0 <= index < len(items) else ()

            def status(index, width):
                view = controller.session.current
                busy = view.state == "loading" or controller.session.navigation is not None
                message = view.message
                snapshot = controller.session.loading_snapshot(feedback=True) if controller.session.navigation is not None else None
                if snapshot is not None:
                    message = snapshot.message if snapshot.visible else ""
                statuses = (FooterStatus(message, StatusKind.LOADING if busy else view.message_kind,
                                        loading_frame() if busy else ""),) if message else ()
                footer = Footer(statuses=statuses, include_cover_status=flags.show_image and index >= 0 and options["provider_id"] != "torrent")
                if flags.show_image and index >= 0 and options["provider_id"] != "torrent":
                    from ..ui.display import _poster_footer_line
                    return _poster_footer_line(ui.hovered_show_obj or {}, footer, width)
                return render_footer(footer, width, theme=theme)

            from ..provider_manifest import PROVIDER_REGISTRY
            screen = dict(presentation, screen_title="Results · " + options["query"], badge=" · ".join((options["provider_name"], *options.get("badges", ()))),
                          layout="top" if options["provider_id"] == "torrent" else presentation.get("layout", "bottom"),
                          reserve_cover=bool(flags.show_image and PROVIDER_REGISTRY.get(options["provider_id"], {}).get("cover_images", True)),
                          context="", summary_noun="results", placeholder="Filter titles…", empty="No matching titles",
                          info_fn=information, status_fn=status,
                          key_aliases={"i": "CTRL_O"}, shortcut_help=(("i", "Title information"),),
                          shortcuts_fn=lambda _index: (("enter", "open"), ("↑↓", "move"), ("/", "filter"),
                                                       ("?", "help"), ("esc", "search")))
            if options["provider_id"] == "torrent":
                from ..ui import torrent_results
                def torrent_shows():
                    return [item.reference for item in controller.session.current.items]
                screen["table_fn"] = lambda width: torrent_results.make_table(torrent_shows(), width)
                screen["summary_fn"] = lambda: torrent_results.source_summary(torrent_shows())
            def picker(*args, **kwargs):
                view = controller.session.current
                screen["empty"] = "Searching titles…" if view.state == "loading" else "No matching titles"
                return action_picker(*args, presentation=screen, **kwargs)
        result = run_view_session(flags, ui, controller.session, picker=picker, theme=theme, updates_fn=controller.updates,
                                  position_key=(controller.session.current.id, options["query"], options["ttype"]),
                                  restore_position=restore_position)
        if not isinstance(result, SearchResultsResult):
            raise TypeError("Search session must return a typed search outcome")
        return result
    finally:
        cancel = getattr(options.get("live_fn"), "cancel", None)
        if callable(cancel):
            cancel()
