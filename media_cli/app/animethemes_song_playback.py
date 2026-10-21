"""Themes collection preparation and the direct-play/return-to-details flow."""

import logging
from dataclasses import replace

from .. import app_core
from ..services import animethemes as service
from ..state.theme import APPLICATION_THEME
from ..ui.footer import StatusKind
from ..ui.transition import PreparationCancelled, call_on_ui_thread
from ..ui.view_model import visible_items
from ..ui.view_picker import run_view_session
from ..ui.view_session import ReplaceView
from .animethemes import _play_row, _row_show, _search_view
from .animethemes_song_files import find_song_file, local_song_row
from .animethemes_song_menu import SongMenuController
from .animethemes_song_title import song_credit, song_player_lines
from .animethemes_song_variants import best_variant

_LOG = logging.getLogger(__name__)


def default_variant(song, quality):
    return best_variant(song["variants"], quality)


def prepare_collection(rows, selected):
    songs = []
    selected_song = None
    for index, row in enumerate(rows):
        theme = row["raw"]
        if "animethemeentries" not in theme:
            theme = service.get_theme(theme["id"])
        anime = theme.get("anime") or {}
        projected = service.anime_song_rows({**anime, "animethemes": [theme]})
        for choice in projected:
            choice["artists"] = choice["artists"] or row.get("artist") or ""
            choice["label"] = service.song_row_label(choice["song"], choice["raw"])
            choice["credit"] = row.get("credit") or {}
        if index == selected:
            if not projected:
                raise ValueError("No playable videos for the selected theme")
            selected_song = len(songs)
        songs.extend(projected)
    if selected_song is None:
        raise ValueError("Selected theme is not in this collection")
    return songs, selected_song


def play_variant(row, flags, cfg, args, show, ui=None, collection=None):
    if not getattr(flags, "plain_mode", False):
        from ..ui.player_screen import _player_ui_state, activate

        app_core.enter_alt_screen()
        activate(show, row.get("theme") or "Song", 1)
        metadata, stream_line = song_player_lines(row)
        credit = song_credit(row.get("song"), row.get("artists"))
        _player_ui_state.update({
            "ttype": "", "current_ep_label": row.get("theme") or "Song",
            "_poster_manager": app_core._poster_manager, "_ui": ui,
            "content_title": row.get("anime") or credit,
            "content_secondary": credit if row.get("anime") else "",
            "content_label": row.get("theme"),
            "content_metadata": metadata, "content_stream": stream_line,
        })
    try:
        return _play_row(row, cfg, args, collection=collection)
    finally:
        if not getattr(flags, "plain_mode", False):
            _player_ui_state.update({"content_title": None, "content_secondary": None, "content_label": None,
                                     "content_metadata": None, "content_stream": None})


def create_song_menu(flags, ui, cfg, args, songs, selected, *, collection_id, session=None, play_callback=None):
    quality = getattr(args, "quality", None) or cfg.get("quality", "best")
    controller = None

    def download(indices):
        from .animethemes_song_download import download_songs

        try:
            rows = tuple(controller.selected_variant(index) or default_variant(controller.songs[index], quality)
                         for index in indices)
            result = download_songs(rows, cfg, args)
        except Exception:
            _LOG.exception("AnimeThemes song download failed")
            message, kind = "Could not add this download. Try again.", StatusKind.ERROR
            result = None
        else:
            message = "Already downloaded. Play uses the local file." if result == "LOCAL" else "Download is in the manager."
            kind = StatusKind.INFO
        if result != "QUIT":
            view = replace(controller.session.current, message=message, message_kind=kind)
            controller.session.apply(ReplaceView(view.id, controller.session.generation, view))
        return result

    def play_collection(song, variant):
        while True:
            row = local_song_row(variant if variant is not None else default_variant(song, quality), cfg)
            anime = song["raw"].get("anime") or {}
            show = _row_show({"kind": "anime", "raw": anime, "label": song.get("anime")})
            index = controller.selected_index
            streams = tuple(controller.selected_variant(number)
                            or default_variant(choice, quality) for number, choice in enumerate(controller.songs))

            def fetch(ordinal, streams=streams):
                target = int(ordinal) - 1
                return (local_song_row(streams[target], cfg)["stream"],) if 0 <= target < len(streams) else None

            collection = {"total_eps": len(controller.songs), "episode_index": index, "fetch_callback": fetch,
                          "start_time_override": controller.start_time,
                          "next_episode": controller.songs[index + 1]["song"] if index + 1 < len(controller.songs) else None}
            try:
                result = (play_callback(row) if play_callback is not None
                          else play_variant(row, flags, cfg, args, show, ui, collection))
            except Exception:
                _LOG.exception("AnimeThemes song playback failed")
                view = replace(controller.session.current, message="Could not play this song. Try another version.",
                               message_kind=StatusKind.ERROR)
                controller.session.apply(ReplaceView(view.id, controller.session.generation, view))
                return
            action = result[0] if isinstance(result, tuple) else None
            position = result[2] if isinstance(result, tuple) and len(result) > 2 else 0
            duration = result[3] if isinstance(result, tuple) and len(result) > 3 else 0
            controller.record_playback(action, position=position, duration=duration)
            if action == "EOF":
                target = controller.next_after_completion()
            elif action in {"NEXT", "PREV"}:
                target = index + (1 if action == "NEXT" else -1)
            else:
                return
            if target is None or not 0 <= target < len(controller.songs):
                return
            controller._activate(target, play=False)
            song, variant = controller.songs[target], controller.selected_variant()

    def play(song, variant):
        try:
            return play_collection(song, variant)
        finally:
            if not app_core.is_termux():
                player = app_core._ipc_player
                if player.process is not None or player.client is not None:
                    try:
                        player.quit()
                    except Exception:
                        _LOG.exception("Could not close the song player")

    interactive_download = not (getattr(flags, "plain_mode", False) or getattr(args, "no_tui", False))
    controller = SongMenuController(songs, play, collection_id=collection_id, selected_index=selected,
                                    session=session, quality=quality, download=download if interactive_download else None,
                                    downloaded=lambda row: bool(find_song_file(row, cfg)))
    return controller


def run_song_collection(flags, ui, cfg, args, songs, selected, *, autoplay=True, collection_id="animethemes:themes"):
    controller = create_song_menu(flags, ui, cfg, args, songs, selected, collection_id=collection_id)
    controller.session.position.selected_id = "action:play"
    if autoplay:
        controller.play(songs[selected], None)
    return run_view_session(flags, ui, controller.session, theme=APPLICATION_THEME)


def open_anime_collection(flags, ui, cfg, args, anime):
    songs = service.anime_song_rows(anime or {})
    if not songs:
        return None
    identity = f"animethemes:anime:{anime.get('slug') or anime.get('id')}"
    return call_on_ui_thread(lambda: run_song_collection(flags, ui, cfg, args, songs, 0,
                                                        autoplay=False, collection_id=identity))


def open_theme_results(flags, ui, ms, cfg, args, rows, selected, query, position):
    view = _search_view(query, "animethemes", ["animethemes"], rows)
    visible = visible_items(view, position.query)
    indices = [item.reference for item in visible]
    if selected not in indices:
        return None
    try:
        songs, initial = app_core.with_loading("Loading songs…", prepare_collection,
                                                [rows[index] for index in indices], indices.index(selected))
    except PreparationCancelled:
        return None
    except Exception:
        _LOG.exception("AnimeThemes song collection loading failed")
        return None
    return call_on_ui_thread(lambda: run_song_collection(flags, ui, cfg, args, songs, initial))
