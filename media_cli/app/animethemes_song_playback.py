"""Themes collection preparation and the direct-play/return-to-details flow."""

import logging
from dataclasses import replace

from .. import app_core
from ..core.api import ProviderDependencyError
from ..services import animethemes as service
from ..state.theme import APPLICATION_THEME
from ..ui.footer import StatusKind
from ..ui.transition import PreparationCancelled, call_on_ui_thread
from ..ui.view_model import visible_items
from ..ui.view_picker import run_view_session
from ..ui.view_session import ReplaceView
from .animethemes import _play_row, _row_show, _search_view, _season_year
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
        identity = show.get("_id") or show.get("id")
        app_core._hovered_show_id = identity
        if ui is not None:
            ui.hovered_show_id = identity
            ui.hovered_show_obj = show
        metadata, stream_line = song_player_lines(row)
        credit = song_credit(row.get("song"), row.get("artists"))
        alternate = show.get("englishName") or ""
        if alternate.casefold() == str(show.get("name") or "").casefold():
            alternate = ""
        season = _season_year({"season": show.get("season"),
                               "year": (show.get("airedStart") or {}).get("year")})
        anime_metadata = " • ".join(part for part in (show.get("type"), season) if part)
        _player_ui_state.update({
            "ttype": "", "current_ep_label": row.get("theme") or "Song",
            "_poster_manager": app_core._poster_manager, "_ui": ui,
            "versions_available": bool(row.get("versions_available")),
            "audio_only": bool(row.get("audio_only")),
            "content_title": row.get("anime") or credit,
            "content_secondary": alternate,
            "content_label": row.get("theme"),
            "content_metadata": anime_metadata,
            "content_stream": " • ".join(part for part in (credit, metadata, stream_line) if part),
        })
    try:
        return _play_row(row, cfg, args, collection=collection)
    finally:
        if not getattr(flags, "plain_mode", False):
            _player_ui_state.update({"content_title": None, "content_secondary": None, "content_label": None,
                                     "content_metadata": None, "content_stream": None,
                                     "audio_only": False, "repeat_mode": None})


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
            from .animethemes_song_variants import version_number

            row = {**row, "audio_only": controller.audio_only,
                   "versions_available": len({version_number(item) for item in song["variants"]}) > 1}
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
            from ..playback.menu import run_playback_view
            from .animethemes_song_variants import version_number

            def playback_menu(kind):
                current = variant if variant is not None else default_variant(song, quality)
                if kind == "v" and len({version_number(item) for item in song["variants"]}) < 2:
                    return "RETURN", None
                view = controller.choices_view("version" if kind == "v" else "quality")
                focused = (f"version:{version_number(current)}" if kind == "v"
                           else f"quality:{song['variants'].index(current)}")
                outcome, selected = run_playback_view(
                    app_core._ipc_player, view, flags, ui, selected_id=focused,
                )
                if selected is None:
                    return outcome, None
                chosen = (best_variant(song["variants"], controller._quality_preferences.get(index, quality), selected)
                          if kind == "v" else song["variants"][selected])
                return outcome, (kind, chosen)

            player = app_core._ipc_player
            previous_menu = getattr(player, "playback_menu", None)
            previous_audio = getattr(player, "audio_only", False)
            previous_repeat = getattr(player, "repeat_control", None)
            player.audio_only = controller.audio_only

            def repeat_control():
                controller._repeat_setting().change()
                from ..ui.player_screen import _player_ui_state
                _player_ui_state.update(repeat_mode=controller.repeat_mode.title(), _cached_w=0)
                return controller.repeat_mode.title()

            from ..ui.player_screen import _player_ui_state
            _player_ui_state["repeat_mode"] = controller.repeat_mode.title()
            player.repeat_control = repeat_control
            player.playback_menu = playback_menu
            try:
                try:
                    result = (play_callback(row) if play_callback is not None
                              else play_variant(row, flags, cfg, args, show, ui, collection))
                finally:
                    player.playback_menu = previous_menu
                    player.audio_only = previous_audio
                    player.repeat_control = previous_repeat
            except ProviderDependencyError:
                raise
            except Exception:
                _LOG.exception("AnimeThemes song playback failed")
                view = replace(controller.session.current, message="Could not play this song. Try another version.",
                               message_kind=StatusKind.ERROR)
                controller.session.apply(ReplaceView(view.id, controller.session.generation, view))
                return
            action = result[0] if isinstance(result, tuple) else None
            position = result[2] if isinstance(result, tuple) and len(result) > 2 else 0
            duration = result[3] if isinstance(result, tuple) and len(result) > 3 else 0
            if action == "MENU_SELECTION":
                kind, variant = player.menu_selection
                controller._variants[index] = song["variants"].index(variant)
                if kind == "m":
                    controller._quality_preferences[index] = variant["stream"].get("resolution")
                controller.start_time = position if kind == "m" else 0
                continue
            if action == "MENU_ENDED":
                controller.record_playback("EOF", position=position, duration=duration)
                return
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
            if not app_core.is_termux() or controller.audio_only:
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


def open_playlist(flags, ui, cfg, args, playlist_id):
    try:
        playlist = app_core.with_loading("Loading playlist…", service.get_playlist, playlist_id)
        songs = service.playlist_song_rows(playlist)
    except PreparationCancelled:
        return None
    except Exception:
        _LOG.exception("AnimeThemes playlist loading failed")
        app_core.set_action_feedback({}, "Could not load playlist. Try again.")
        return None
    if not songs:
        app_core.set_action_feedback({}, "No playable songs in this playlist.")
        return None
    return call_on_ui_thread(lambda: run_song_collection(
        flags, ui, cfg, args, songs, 0, autoplay=False,
        collection_id=f"animethemes:playlist:{playlist_id}",
    ))


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
