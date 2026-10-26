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


def run_search_results(flags, ui, *, restore_position=True, **options) -> SearchResultsResult:
    try:
        controller = SearchResultsController(ui=ui, **options)
        result = run_view_session(flags, ui, controller.session, theme=APPLICATION_THEME, updates_fn=controller.updates,
                                  position_key=(controller.session.current.id, options["query"], options["ttype"]),
                                  restore_position=restore_position)
        if not isinstance(result, SearchResultsResult):
            raise TypeError("Search session must return a typed search outcome")
        return result
    finally:
        cancel = getattr(options.get("live_fn"), "cancel", None)
        if callable(cancel):
            cancel()
