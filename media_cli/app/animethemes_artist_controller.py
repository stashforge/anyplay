"""Artist data and actions expressed as provider-independent terminal views."""

from dataclasses import replace
import logging
from itertools import count
from threading import Condition, Lock, Thread

from ..services import animethemes as service
from ..ui.view_model import Blank, Content, Count, DetailLines, ImageSpec, Inherit, LabelValue, SelectionPolicy, ViewAction, ViewItem, ViewSpec
from ..ui.view_session import BeginNavigation, CompleteNavigation, ExitSession, FailNavigation, ReplaceView, UpdateItems, ViewSession
from .animethemes import _image_url, _row_detail_values

_LOG = logging.getLogger(__name__)
_CONTEXT = ("AnimeThemes", "Artist")
_OPEN = (ViewAction("select", "open", ("ENTER", "RIGHT")),)
_PLAY = (ViewAction("select", "play", ("ENTER", "RIGHT")),)


def _count(number, noun):
    return f"{number} {noun}{'' if number == 1 else 's'}"


class _ArtistJobs:
    """Keep current hover work bounded and give explicit navigation its own worker."""

    def __init__(self):
        self._condition = Condition()
        self._pending = {}
        self._active = set()
        self._closed = False
        self.threads = []

    @property
    def pending_count(self):
        with self._condition:
            return len(self._pending)

    def submit(self, lane, key, function, args, valid):
        with self._condition:
            if self._closed or key in self._active:
                return
            self._pending[lane] = (key, function, args, valid)
            if not self.threads:
                for name in ("hover", "hover", "navigation"):
                    thread = Thread(target=self._run, args=(name,), daemon=True, name=f"artist-{name}")
                    self.threads.append(thread)
                    thread.start()
            self._condition.notify_all()

    def _run(self, lane):
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._closed or lane in self._pending)
                if self._closed:
                    return
                key, function, args, valid = self._pending.pop(lane)
                self._active.add(key)
            try:
                if valid():
                    function(*args)
            except Exception:
                _LOG.exception("Artist background job failed")
            finally:
                with self._condition:
                    self._active.discard(key)

    def close(self):
        with self._condition:
            self._closed = True
            self._pending.clear()
            self._condition.notify_all()


class ArtistController:
    def __init__(self, artist, play_callback, *, source_view=None, source_position=None, song_menu_factory=None):
        self.artist = dict(artist)
        self.play = play_callback
        self.root_id = f"artist:{artist['slug']}"
        self._jobs = _ArtistJobs()
        self._cache_lock = Lock()
        self._song_locks = {}
        self._cache = {}
        self._closed = False
        self._artist_loaded = False
        self._song_menu_factory = song_menu_factory
        self._song_menus = {}
        self._requests = count(1)
        self._external_source = source_view is not None
        if source_view is None:
            row = {"kind": "artist", "raw": artist, "label": artist.get("name") or "Unknown artist"}
            source_view = ViewSpec(f"{self.root_id}:source", "Search AnimeThemes", (
                ViewItem("artist", row["label"], kind="submenu", details=Inherit, image=self.portrait()),
            ), details=DetailLines(*(Content(value) if value else Blank for value in _row_detail_values(row))),
                context=_CONTEXT, actions=_OPEN)
        self.source_id = source_view.id
        self.session = ViewSession(source_view, self.handle_event)
        if source_position is not None:
            position = self.session.position
            position.query = source_position.query
            position.scroll = source_position.scroll
            if 0 <= source_position.selected_index < len(source_view.items):
                position.selected_id = source_view.items[source_position.selected_index].id
        selected = self.session.item()
        self._source_index = selected.reference if selected is not None else None

    def portrait(self):
        url = _image_url(self.artist)
        state = "available" if url else "unavailable" if self._artist_loaded else "resolving"
        return ImageSpec(self.root_id, state, url or None)

    def sections_view(self, sections, *, state="ready", message=""):
        direct = len(service.artist_theme_rows(self.artist))
        metadata = " • ".join(part for part in (
            f"{direct} direct {'theme' if direct == 1 else 'themes'}" if "songs" in self.artist else "",
            _count(len(self.artist.get("groups") or []), "group") if "groups" in self.artist else "",
        ) if part)
        title = f"Artist: {self.artist.get('name') or 'Unknown artist'}"
        items = []
        for section in sections:
            section_id = section.get("id") or section.get("group") or "direct"
            secondary = section["label"]
            if section.get("credit"):
                secondary += " • As " + section["credit"]
            details = DetailLines(Inherit, Content(secondary), Inherit)
            items.append(ViewItem(f"section:{section_id}", section["label"], kind="submenu",
                                  hint=_count(len(section["rows"]), "theme"), details=details,
                                  info="\n".join((title, secondary, metadata)), reference=section))
        return ViewSpec(self.root_id, "Select group", tuple(items),
                        details=DetailLines(Content(LabelValue("Artist", self.artist.get("name") or "Unknown artist")),
                                            Content(self.artist["information"]) if self.artist.get("information") else Blank,
                                            Content(metadata) if metadata else Blank),
                        image=self.portrait(), actions=_OPEN, context=_CONTEXT,
                        state=state if items or state != "ready" else "empty",
                        message=message or ("No artist themes found" if not items else ""),
                        selection_policy=SelectionPolicy(auto_single=True))

    def song_item(self, row, section, *, resolved=False):
        raw = row["raw"]
        song = raw.get("song") or {}
        name = self.artist.get("name") or "Unknown artist"
        credits = [f"Artist: {name}"]
        role = (row.get("credit") or {}).get("as") or section.get("credit")
        if role:
            credits.append("As " + role)
        others = []
        for performer in song.get("artists") or []:
            performer_name = performer.get("name")
            if not performer_name or performer_name in {name, section.get("group")}:
                continue
            character = (performer.get("artistsong") or {}).get("as")
            others.append(f"{character} (CV: {performer_name})" if character else performer_name)
        if others:
            credits.append("With " + ", ".join(others))
        elif section.get("group"):
            credits.append(section["group"])
        anime = raw.get("anime") or {}
        theme = str(raw.get("type") or "") + str(raw.get("sequence") or "")
        from .animethemes_song_title import theme_result_lines

        lines = theme_result_lines({**raw, "slug": raw.get("slug") or theme}, artist=name, extras=credits[1:])
        details = DetailLines(*(Content(text) if text else Blank for text in lines))
        url = _image_url(anime)
        image = ImageSpec(f"anime:{anime.get('slug') or raw['id']}",
                          "available" if url else "unavailable" if resolved else "resolving", url or None)
        label = service.song_row_label(song.get("title"), raw)
        return ViewItem(f"theme:{raw['id']}", label, kind="submenu", details=details, image=image,
                        reference=(row, section))

    def songs_view(self, section):
        section_id = section.get("id") or section.get("group") or "direct"
        items = tuple(self.song_item(row, section) for row in section["rows"])
        return ViewSpec(f"{self.root_id}:songs:{section_id}", "Select song", items,
                        details=DetailLines(Content(LabelValue("Artist", self.artist['name'])), Content(section["label"]),
                                            Content(Count(len(items), "theme"))),
                        context=_CONTEXT, actions=_PLAY, state="ready" if items else "empty",
                        message="No themes found" if not items else "")

    def install_sections(self, sections):
        self._artist_loaded = True
        self.session.apply(ReplaceView(self.session.current.id, self.session.generation, self.sections_view(sections)))

    def _submit(self, function, *args, hover=False, target=None):
        if not self._closed:
            view_id, generation = target or (self.session.current.id, self.session.generation)
            identity = args[-1].id if hover else args[-1]
            key = (view_id, generation, identity)
            def valid():
                if self._closed or self.session.frame(view_id, generation) is None:
                    return False
                navigation = self.session.navigation
                return hover or (navigation is not None and navigation.request_id == identity)
            self._jobs.submit("hover" if hover else "navigation", key, function, args,
                              valid)

    def start(self):
        source, generation = self.session.current.id, self.session.generation
        request = f"artist:{next(self._requests)}"
        self.session.apply(BeginNavigation(source, generation, request, "Fetching artist performances"))
        self._submit(self._load_artist, source, generation, request)

    def _fail_view(self, view_id, generation, message):
        frame = self.session.frame(view_id, generation)
        if frame is None:
            return
        image = frame.view.image
        if image is not None and image.state == "resolving":
            image = replace(image, state="failed")
        self.session.post(ReplaceView(view_id, generation,
                                      replace(frame.view, state="failed", message=message, image=image)))

    def _load_artist(self, source_id, generation, request):
        try:
            artist = service.get_artist(self.artist["slug"])
            sections = service.artist_sections(artist)
            self.artist = artist
            self._artist_loaded = True
            self.session.post(CompleteNavigation(source_id, generation, request, self.sections_view(sections),
                                                  replace_current=True))
        except Exception:
            _LOG.exception("Artist performance loading failed")
            self.session.post(FailNavigation(source_id, generation, request, "Could not load artist performances"))

    def _enriched(self, row):
        theme_id = row["raw"]["id"]
        with self._cache_lock:
            lock = self._song_locks.setdefault(theme_id, Lock())
        with lock:
            if theme_id not in self._cache:
                self._cache[theme_id] = service.enrich_artist_theme(row)["raw"]
            return {**row, "raw": self._cache[theme_id]}

    def enrich_item(self, view_id, generation, item):
        row, section = item.reference
        try:
            updated = self.song_item(self._enriched(row), section, resolved=True)
            _LOG.debug("Artist theme ready: id=%s cover=%s", item.id, updated.image.state)
        except Exception:
            _LOG.exception("Artist theme loading failed: %s", item.id)
            updated = replace(item, image=replace(item.image, state="failed", message="Could not fetch cover"))
        self.session.post(UpdateItems(view_id, generation, (updated,), expected_images=((item.id, item.image),)))

    def _load_section(self, view_id, generation, section, request, selected=0):
        try:
            from .animethemes_song_playback import create_song_menu, prepare_collection

            rows = [self._enriched(row) for row in section["rows"]]
            songs, initial = prepare_collection(rows, selected)
            identity = f"{self.root_id}:section:{section.get('id') or section.get('group') or 'direct'}"
            menu = (self._song_menu_factory(songs, initial, identity, self.session) if self._song_menu_factory
                    else create_song_menu(None, None, {}, None, songs, initial, collection_id=identity,
                                          session=self.session, play_callback=self.play))
            self._song_menus[menu.root_id] = menu
            self.session.post(CompleteNavigation(view_id, generation, request, menu.details_view()))
        except Exception:
            _LOG.exception("Artist songs loading failed")
            self.session.post(FailNavigation(view_id, generation, request, "Could not load these songs"))

    def _load_videos(self, view_id, generation, item, request):
        row, section = item.reference
        selected = next(index for index, candidate in enumerate(section["rows"])
                        if candidate["raw"]["id"] == row["raw"]["id"])
        self._load_section(view_id, generation, section, request, selected)

    def handle_event(self, event):
        for identity, menu in tuple(self._song_menus.items()):
            if event.view_id == identity or event.view_id.startswith(identity + ":"):
                return menu.handle_event(event)
        if event.view_id == self.source_id:
            if event.kind == "select":
                selected = self.session.item(event.item_id)
                if selected is not None and (selected.reference is None or selected.reference == self._source_index):
                    self.start()
                    return None
            if event.kind in {"select", "back", "quit", "tab", "query"}:
                return ExitSession(event if self._external_source else event.kind)
            return None
        if event.kind in {"back", "quit"}:
            return ExitSession(event.kind)
        item = self.session.item(event.item_id)
        if item is None:
            return None
        if event.kind == "focus" and item.id.startswith("theme:"):
            if item.image.state == "resolving":
                self._submit(self.enrich_item, event.view_id, event.generation, item, hover=True)
        elif event.kind == "select":
            if item.id.startswith("section:"):
                request = f"songs:{next(self._requests)}"
                self.session.apply(BeginNavigation(event.view_id, event.generation, request, "Fetching songs"))
                self._submit(self._load_section, event.view_id, event.generation, item.reference, request)
            if item.id.startswith("theme:"):
                request = f"videos:{next(self._requests)}"
                self.session.apply(BeginNavigation(event.view_id, event.generation, request, "Fetching song videos"))
                self._submit(self._load_videos, event.view_id, event.generation, item, request)
            elif item.id.startswith("video:"):
                self.play(item.reference)
        return None

    def close(self):
        self._closed = True
        self._jobs.close()
