"""Ready-data song menus; playback resolution stays with the application caller."""

from dataclasses import replace
from math import isfinite

from ..ui.setting_control import ChoiceSetting, toggle_setting
from ..ui.styled_text import StyledText, TextRun
from ..ui.view_model import (
    Blank,
    Content,
    DetailLines,
    ImageSpec,
    Inherit,
    SelectionPolicy,
    ViewAction,
    ViewItem,
    ViewSpec,
)
from ..ui.view_session import ExitSession, PopView, PushView, ReplaceView, ViewSession
from .animethemes import _image_url
from .animethemes_song_title import song_player_lines, theme_result_lines
from .animethemes_song_variants import best_variant, version_number
from .details_menu_view import DetailsMenuOption

_ACTIONS = (
    ViewAction("select", "select", ("ENTER", "RIGHT")),
    ViewAction("back", "back", ("ESC", "LEFT")),
)


def _details(song):
    credit = (song.get("credit") or {}).get("as")
    lines = theme_result_lines(song["raw"], artist=song.get("artists"),
                               extras=("As " + credit,) if credit else ())
    return DetailLines(*(Content(text) if text else Blank for text in lines))


def _image(song, key):
    url = _image_url(song.get("raw") or {})
    return ImageSpec(key, "available", url) if url else None


class SongMenuController:
    def __init__(self, songs, play, *, collection_id, selected_index=0, session=None, quality="best", download=None,
                 downloaded=None):
        self.songs = tuple(songs)
        if type(selected_index) is not int or not 0 <= selected_index < len(self.songs):
            raise ValueError("Song menu requires a selected song in its collection")
        if any(not song.get("variants") for song in self.songs):
            raise ValueError("Song menu requires playable variants for every song")
        if not callable(play):
            raise TypeError("Song menu requires a playback callback")
        if download is not None and not callable(download):
            raise TypeError("Song download requires a callback")
        self.play = play
        self.download = download
        if downloaded is not None and not callable(downloaded):
            raise TypeError("Downloaded state requires a callback")
        self.downloaded = downloaded
        self._download_marks = set()
        self.root_id = f"{collection_id}:song-details"
        self.selected_index = selected_index
        self._variants = {}
        self.quality = quality
        self._quality_preferences = {}
        self._finished = set()
        self._resume = {}
        self.start_time = 0
        self.auto_play_next = False
        self.repeat_mode = "off"
        self._once_repeated = False
        self.session = session if session is not None else ViewSession(self.details_view(), self.handle_event)

    def details_view(self):
        song = self.songs[self.selected_index]
        current = self.selected_variant() or best_variant(song["variants"], self.quality)
        versions = {version_number(row) for row in song["variants"]}
        files = [row for row in song["variants"] if version_number(row) == version_number(current)]
        finished = self.selected_index in self._finished
        has_next = self.selected_index + 1 < len(self.songs)
        resume = self._resume.get(self.selected_index, 0)
        play_label = "Play Next" if finished and has_next else "Replay" if finished else "Continue" if resume else "Play"
        play_hint = (f"play {self.songs[self.selected_index + 1]['label']}" if finished and has_next
                     else f"play {song['label']} again" if finished
                     else f"resume {song['label']} from {int(resume) // 60}:{int(resume) % 60:02d}" if resume
                     else f"play {song['label']} from start")
        options = (
            DetailsMenuOption("play", play_label, hint=play_hint),
            DetailsMenuOption("replay", "Replay", enabled=(finished and has_next) or bool(resume),
                              hint=f"play {song['label']} again"),
            DetailsMenuOption("previous", "Previous", enabled=self.selected_index > 0,
                              hint=f"play {self.songs[self.selected_index - 1]['label']}"
                              if self.selected_index > 0 else ""),
            DetailsMenuOption("next", "Next", enabled=has_next and not finished,
                              hint=f"play {self.songs[self.selected_index + 1]['label']}"
                              if self.selected_index + 1 < len(self.songs) else ""),
            DetailsMenuOption("songs", "Songs", opens_menu=True),
            DetailsMenuOption("version", "Version", enabled=len(versions) > 1, opens_menu=True,
                              hint="Original" if version_number(current) == 1 else f"Version {version_number(current)}"),
            DetailsMenuOption("quality", "Quality", enabled=len(files) > 1, opens_menu=True,
                              hint=song_player_lines(current)[1]),
            DetailsMenuOption("download", "Download", enabled=self.download is not None,
                              hint=f"download {song['label']}", opens_menu=len(self.songs) > 1),
            DetailsMenuOption("playback", "Playback", opens_menu=True),
        )
        return ViewSpec(
            self.root_id, "Select action",
            tuple(ViewItem(f"action:{option.action_id}", option.label,
                           kind="submenu" if option.opens_menu else "item", hint=option.hint,
                           enabled=option.enabled, details=Inherit, reference=option.action_id)
                  for option in options if option.enabled),
            details=_details(song), image=_image(song, f"{self.root_id}:song:{self.selected_index}"),
            context=("AnimeThemes",), actions=_ACTIONS,
        )

    def record_playback(self, outcome, *, position=0, duration=0):
        if outcome == "EOF":
            self._finished.add(self.selected_index)
            self._resume.pop(self.selected_index, None)
        elif outcome in {"QUIT", "NEXT", "PREV"}:
            self._finished.discard(self.selected_index)
            self._resume.pop(self.selected_index, None)
            if outcome == "QUIT":
                try:
                    position, duration = float(position), float(duration or 0)
                except (TypeError, ValueError, OverflowError):
                    position, duration = 0, 0
                if isfinite(position) and position > 0 and isfinite(duration) and (duration <= 0 or position < duration):
                    self._resume[self.selected_index] = position
        else:
            return
        self.session.apply(ReplaceView(self.root_id, self.session.generation, self.details_view()))

    def _song_item(self, index):
        song = self.songs[index]
        variant = self.selected_variant(index) or best_variant(song["variants"], self.quality)
        available = self.downloaded is not None and self.downloaded(variant)
        label = song["label"]
        if available:
            label = StyledText((TextRun(label, "episode.label"), TextRun("  \u2714", "episode.downloaded")))
        return ViewItem(f"song:{index}", label, details=_details(song), reference=index,
                        hint="downloaded" if available else "",
                        image=_image(song, f"{self.root_id}:song:{index}"))

    def songs_view(self):
        return ViewSpec(
            f"{self.root_id}:songs", "Select song",
            tuple(self._song_item(index) for index in range(len(self.songs))),
            details=DetailLines(Blank, Blank, Blank), context=("AnimeThemes",), actions=_ACTIONS,
        )

    def download_selection_view(self):
        return replace(self.songs_view(), id=f"{self.root_id}:download-select", prompt="Select songs to download",
                       selection_policy=SelectionPolicy(multiple=True),
                       actions=(ViewAction("select", "download", ("ENTER",)), _ACTIONS[1]))

    def download_view(self):
        song = self.songs[self.selected_index]
        return ViewSpec(
            f"{self.root_id}:download", "Download",
            (ViewItem("download:current", "Download Current", hint=f"download {song['label']}",
                      details=Inherit, reference="current"),
             ViewItem("download:select", "Select Songs", kind="submenu", details=Inherit, reference="select"),
             ViewItem("download:all", "Download All", hint=f"download {len(self.songs)} songs",
                      details=Inherit, reference="all")),
            details=_details(song), image=_image(song, f"{self.root_id}:song:{self.selected_index}"),
            context=("AnimeThemes",), actions=_ACTIONS,
        )

    def _set_auto_play_next(self, value):
        self.auto_play_next = value

    def _auto_next_setting(self):
        return toggle_setting("setting:auto-next", "Auto-Play Next", self.auto_play_next,
                              self._set_auto_play_next, hint="play the next song when this one ends")

    def _set_repeat_mode(self, value):
        self.repeat_mode = value
        self._once_repeated = False

    def _repeat_setting(self):
        hints = {
            "off": "no extra replays",
            "once": "replay once, then follow Auto-Play Next",
            "song": "keep replaying this song",
            "collection": "play all songs in a loop",
        }
        return ChoiceSetting("setting:repeat", "Repeat", self.repeat_mode,
                             (("off", "Off"), ("once", "Once"), ("song", "Song"), ("collection", "Collection")),
                             self._set_repeat_mode, hint=hints[self.repeat_mode])

    def next_after_completion(self):
        if self.repeat_mode == "song":
            return self.selected_index
        if self.repeat_mode == "collection":
            return (self.selected_index + 1) % len(self.songs)
        if self.repeat_mode == "once" and not self._once_repeated:
            self._once_repeated = True
            return self.selected_index
        if self.auto_play_next and self.selected_index + 1 < len(self.songs):
            return self.selected_index + 1
        return None

    def playback_view(self):
        song = self.songs[self.selected_index]
        return ViewSpec(
            f"{self.root_id}:playback", "Playback",
            (self._auto_next_setting().item(), self._repeat_setting().item()),
            details=_details(song), image=_image(song, f"{self.root_id}:song:{self.selected_index}"),
            context=("AnimeThemes",), actions=_ACTIONS + (
                ViewAction("increase", "Next value", footer=False),
                ViewAction("decrease", "Previous value", footer=False),
            ), help=(("Enter", "Cycle selected setting"),
                     ("Left Arrow", "Previous value at filter start; otherwise edit filter"),
                     ("Right Arrow", "Next value at filter end; otherwise edit filter"),
                     ("Esc", "Back")),
        )

    def choices_view(self, kind):
        song = self.songs[self.selected_index]
        current = self.selected_variant() or best_variant(song["variants"], self.quality)
        if kind == "version":
            items = tuple(ViewItem(f"version:{number}", "Original" if number == 1 else f"Version {number}",
                                   details=Inherit, reference=number)
                          for number in sorted({version_number(row) for row in song["variants"]}))
        else:
            items = tuple(ViewItem(f"quality:{index}", song_player_lines(row)[1] or "Default",
                                   details=Inherit, reference=index)
                          for index, row in enumerate(song["variants"])
                          if version_number(row) == version_number(current))
        return ViewSpec(
            f"{self.root_id}:{kind}:{self.selected_index}", f"Select {kind}", items,
            details=_details(song), image=_image(song, f"{self.root_id}:song:{self.selected_index}"),
            context=("AnimeThemes",), actions=_ACTIONS,
        )

    def _activate(self, index, *, play, resume=False):
        if self.session.current.id != self.root_id:
            self.session.apply(PopView())
        if index != self.selected_index:
            self._once_repeated = False
        self.selected_index = index
        self.start_time = self._resume.get(index, 0) if resume else 0
        if play and not self.start_time:
            self._once_repeated = False
        self.session.apply(ReplaceView(self.root_id, self.session.generation, self.details_view()))
        if play:
            self.session.position.selected_id = "action:play"
            song = self.songs[index]
            variant_index = self._variants.get(index)
            self.play(song, song["variants"][variant_index] if variant_index is not None else None)

    def selected_variant(self, song_index=None):
        song_index = self.selected_index if song_index is None else song_index
        index = self._variants.get(song_index)
        return self.songs[song_index]["variants"][index] if index is not None else None

    def handle_event(self, event):
        if event.kind in {"back", "quit"}:
            return ExitSession(event.kind)
        adjusting = event.kind == "action" and event.value in {"increase", "decrease"}
        if event.kind != "select" and not adjusting:
            return None
        if event.view_id == f"{self.root_id}:download-select" and event.kind == "select":
            marked = set(event.value if isinstance(event.value, tuple) else (event.item_id,))
            indices = tuple(index for index in range(len(self.songs)) if f"song:{index}" in marked)
            if not indices:
                return None
            self._download_marks = marked
            self.session.apply(PopView())
            result = self.download(indices)
            return ExitSession("quit") if result == "QUIT" else None
        item = self.session.item(event.item_id)
        if item is None or not item.selectable:
            return None
        if event.view_id == f"{self.root_id}:playback":
            setting = next((setting for setting in (self._auto_next_setting(), self._repeat_setting())
                            if setting.id == event.item_id), None)
            if setting is not None and setting.handle_event(event):
                self.session.apply(ReplaceView(event.view_id, self.session.generation, self.playback_view()))
            return None
        if adjusting:
            return None
        if event.view_id == self.root_id:
            if item.reference == "download":
                if len(self.songs) > 1:
                    self.session.apply(PushView(self.download_view()))
                    return None
                result = self.download((self.selected_index,))
                return ExitSession("quit") if result == "QUIT" else None
            if item.reference == "playback":
                self.session.apply(PushView(self.playback_view()))
                return None
            if item.reference == "songs":
                self.session.apply(PushView(self.songs_view()))
                self.session.position.selected_id = f"song:{self.selected_index}"
                return None
            if item.reference in {"version", "quality"}:
                song = self.songs[self.selected_index]
                current = self.selected_variant() or best_variant(song["variants"], self.quality)
                value = version_number(current) if item.reference == "version" else song["variants"].index(current)
                self.session.apply(PushView(self.choices_view(item.reference)))
                self.session.position.selected_id = f"{item.reference}:{value}"
                return None
            index = self.selected_index
            if item.reference == "previous":
                index -= 1
            elif item.reference == "next" or (
                item.reference == "play" and index in self._finished and index + 1 < len(self.songs)
            ):
                index += 1
            self._activate(index, play=True, resume=item.reference == "play" and index == self.selected_index)
        elif event.view_id == f"{self.root_id}:songs":
            self._activate(item.reference, play=True)
        elif event.view_id == f"{self.root_id}:download":
            if item.reference == "select":
                self.session.apply(PushView(self.download_selection_view()))
                self.session.position.marked_ids = self._download_marks.copy()
                self.session.position.selected_id = f"song:{self.selected_index}"
                return None
            indices = tuple(range(len(self.songs))) if item.reference == "all" else (self.selected_index,)
            result = self.download(indices)
            return ExitSession("quit") if result == "QUIT" else None
        else:
            song = self.songs[self.selected_index]
            current = self.selected_variant() or best_variant(song["variants"], self.quality)
            if item.id.startswith("version:"):
                preference = self._quality_preferences.get(self.selected_index, self.quality)
                chosen = best_variant(song["variants"], preference, item.reference)
                variant_index = song["variants"].index(chosen)
            else:
                variant_index = item.reference
                self._quality_preferences[self.selected_index] = song["variants"][variant_index]["stream"].get("resolution")
            if song["variants"][variant_index] != current:
                self._resume.pop(self.selected_index, None)
                self._finished.discard(self.selected_index)
            self._variants[self.selected_index] = variant_index
            self._activate(self.selected_index, play=False)
        return None
