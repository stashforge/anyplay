"""Series browsing through the shared navigation and loading lifecycle."""

import logging
from itertools import count
from threading import Thread

from ..services import animethemes as service
from ..state.theme import APPLICATION_THEME
from ..ui.view_model import Blank, Content, Count, DetailLines, ImageSpec, ViewAction, ViewItem, ViewSpec
from ..ui.view_session import BeginNavigation, CompleteNavigation, ExitSession, FailNavigation, ViewSession
from .animethemes import _image_url, _play_row, _row_detail_values

_LOG = logging.getLogger(__name__)
_OPEN = (ViewAction("select", "open", ("ENTER", "RIGHT")),)
_PLAY = (ViewAction("select", "play", ("ENTER", "RIGHT")),)


class SeriesController:
    def __init__(self, series, play, *, source_view, source_position, song_menu_factory=None, collection_kind="series"):
        self.series = dict(series)
        self.collection_kind = collection_kind
        self.collection_label = collection_kind.title()
        self.play = play
        self.source_id = source_view.id
        self.session = ViewSession(source_view, self.handle_event)
        self._requests = count(1)
        self._closed = False
        self._cache = {}
        self._song_menus = {}
        self._song_menu_factory = song_menu_factory
        position = self.session.position
        position.query, position.scroll = source_position.query, source_position.scroll
        if 0 <= source_position.selected_index < len(source_view.items):
            position.selected_id = source_view.items[source_position.selected_index].id
        selected = self.session.item()
        self._source_item = selected.id if selected else None

    def _load(self, message, build):
        session = self.session
        view_id, generation = session.current.id, session.generation
        request = f"series:{next(self._requests)}"
        session.apply(BeginNavigation(view_id, generation, request, message))

        def worker():
            try:
                view = build()
                command = CompleteNavigation(view_id, generation, request, view)
            except Exception:
                _LOG.exception("AnimeThemes Series navigation failed")
                command = FailNavigation(view_id, generation, request, "Could not load this selection. Try again.")
            if not self._closed:
                session.post(command)

        Thread(target=worker, daemon=True, name="animethemes-series").start()

    def start(self):
        self._load(f"Fetching {self.collection_kind} titles", self._titles)

    def _titles(self):
        key = (self.collection_kind, self.series["slug"])
        if key not in self._cache:
            loader = service.get_studio if self.collection_kind == "studio" else service.get_series
            self._cache[key] = loader(self.series["slug"])
        series = self._cache[key]
        items = {}
        for anime in series.get("anime") or []:
            slug = anime.get("slug")
            if not slug:
                continue
            row = {"kind": "anime", "raw": anime, "label": anime.get("name") or slug}
            url = _image_url(anime)
            items[slug] = ViewItem(
                f"anime:{slug}", row["label"], kind="submenu", reference=anime,
                details=DetailLines(*(Content(text) if text else Blank for text in _row_detail_values(row))),
                image=ImageSpec(f"anime:{slug}", "available", url) if url else ImageSpec(f"anime:{slug}", "unavailable"),
                info=anime.get("synopsis") or "",
            )
        return ViewSpec(
            f"{self.collection_kind}:{self.series['slug']}", "Select anime", tuple(items.values()),
            details=DetailLines(Content(series.get("name") or self.series.get("name") or "Series"),
                                Blank, Content(Count(len(items), "anime"))),
            context=("AnimeThemes", self.collection_label), actions=_OPEN,
            state="ready" if items else "empty", message=f"No anime found in this {self.collection_kind}" if not items else "",
        )

    def _videos(self, anime):
        slug = anime["slug"]
        key = ("anime", slug)
        if key not in self._cache:
            themes = anime.get("animethemes")
            complete = isinstance(themes, list) and all(
                isinstance(theme.get("animethemeentries"), list) for theme in themes
            )
            detail = anime if complete else service.get_anime_for_show({"_animethemes_slug": slug})
            if not detail:
                raise ValueError("Anime detail unavailable")
            self._cache[key] = detail
        detail = self._cache[key]
        songs = service.anime_song_rows(detail)
        if songs:
            from .animethemes_song_playback import create_song_menu

            identity = f"{self.collection_kind}:{self.series['slug']}:anime:{slug}"
            menu = (self._song_menu_factory(songs, 0, identity, self.session) if self._song_menu_factory
                    else create_song_menu(None, None, {}, None, songs, 0, collection_id=identity,
                                          session=self.session, play_callback=self.play))
            self._song_menus[menu.root_id] = menu
            return menu.details_view()
        raw_row = {"kind": "anime", "raw": detail, "label": detail.get("name") or slug}
        url = _image_url(detail)
        return ViewSpec(
            f"{self.collection_kind}:{self.series['slug']}:anime:{slug}", "Select song", (),
            details=DetailLines(*(Content(text) if text else Blank for text in _row_detail_values(raw_row))),
            image=ImageSpec(f"anime:{slug}", "available", url) if url else ImageSpec(f"anime:{slug}", "unavailable"),
            context=("AnimeThemes", self.collection_label), actions=_PLAY,
            state="empty", message="No theme videos found",
        )

    def handle_event(self, event):
        for identity, menu in tuple(self._song_menus.items()):
            if event.view_id == identity or event.view_id.startswith(identity + ":"):
                return menu.handle_event(event)
        if event.view_id == self.source_id:
            if event.kind == "select" and event.item_id == self._source_item:
                self.start()
            elif event.kind in {"select", "tab", "back", "quit"}:
                return ExitSession(event)
            return None
        if event.kind in {"back", "quit"}:
            return ExitSession(event.kind)
        item = self.session.item(event.item_id)
        if event.kind == "select" and item is not None:
            if item.id.startswith("anime:"):
                self._load("Fetching theme videos", lambda: self._videos(item.reference))
            elif item.id.startswith("video:"):
                self.play(item.reference)
        return None

    def close(self):
        self._closed = True


def open_series(flags, ui, ms, cfg, args, raw, *, source_view, source_position, collection_kind="series"):
    from ..ui.view_picker import run_view_session
    from .animethemes_song_playback import create_song_menu

    def song_menu(songs, selected, identity, session):
        return create_song_menu(flags, ui, cfg, args, songs, selected, collection_id=identity, session=session)

    controller = SeriesController(raw, lambda row: _play_row(row, cfg, args),
                                  source_view=source_view, source_position=source_position,
                                  song_menu_factory=song_menu, collection_kind=collection_kind)
    try:
        controller.start()
        return run_view_session(flags, ui, controller.session, theme=APPLICATION_THEME)
    finally:
        if controller.session.current.id == controller.source_id:
            position = controller.session.position
            source_position.query, source_position.scroll = position.query, position.scroll
            selected = controller.session.item()
            if selected is not None and isinstance(selected.reference, int):
                source_position.selected_index = selected.reference
        controller.close()
