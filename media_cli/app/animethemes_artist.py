"""Enter the shared UI session for an AnimeThemes artist."""

from .animethemes import _play_row
from .animethemes_artist_controller import ArtistController


def open_artist(flags, ui, ms, cfg, args, raw, *, source_view=None, source_position=None):
    from ..state.theme import APPLICATION_THEME
    from ..ui.view_picker import run_view_session
    from .animethemes_song_playback import create_song_menu

    def song_menu(songs, selected, identity, session):
        return create_song_menu(flags, ui, cfg, args, songs, selected, collection_id=identity, session=session)

    controller = ArtistController(raw, lambda row: _play_row(row, cfg, args),
                                  source_view=source_view, source_position=source_position,
                                  song_menu_factory=song_menu)
    try:
        controller.start()
        return run_view_session(flags, ui, controller.session, theme=APPLICATION_THEME)
    finally:
        if source_position is not None and controller.session.current.id == controller.source_id:
            position = controller.session.position
            source_position.query, source_position.scroll = position.query, position.scroll
            selected = controller.session.item()
            if selected is not None and isinstance(selected.reference, int):
                source_position.selected_index = selected.reference
        controller.close()
