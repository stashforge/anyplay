"""AniList root-menu presentation."""


from .transition import PreparationCancelled


class AniListTabChanged(PreparationCancelled):
    """Abandon a pending list screen while its cached fetch may finish."""
    def __init__(self, status):
        self.status = status
        super().__init__(status)


LIST_STATUSES = {
    "Airing": "ANILIST_AIRING",
    "Watching": "CURRENT",
    "Plan to Watch": "PLANNING",
    "Completed": "COMPLETED",
    "Rewatching": "REPEATING",
    "Paused": "PAUSED",
    "Dropped": "DROPPED",
    "Search": "ANILIST_SEARCH",
}


LIBRARY_TABS = (("CURRENT", "Watching"), ("PLANNING", "Planning"), ("COMPLETED", "Completed"),
                ("REPEATING", "Rewatching"), ("PAUSED", "Paused"), ("DROPPED", "Dropped"), ("ALL", "All"))

def menu_header(width=80):
    from .panels import render_menu_card
    return render_menu_card(
        "Choose an AniList list.",
        "Titles are matched to streaming providers before playback.",
        "Enter=select  Esc=quit",
        width=width,
    )


def menu_navigation(result):
    if result == -2:
        return "QUIT"
    if result == -3:
        return "SEARCH"
    return None


def loading_frame(status, rows, columns, loading_text=""):
    """Use the standard list compositor while a requested list is loading."""
    from .list_selection import ListSelection
    from .list_screen import list_screen_frame
    from .frame_surface import compose
    from .navigation_tabs import NavigationTabs
    from .view_model import ViewTab
    airing = status == 'ANILIST_AIRING'
    tabs = (('today', 'Today'), ('tomorrow', 'Tomorrow'), ('week', 'Next 5 Days')) if airing else LIBRARY_TABS
    active = 'today' if airing else (status or 'ALL')
    if active not in dict(tabs):
        active = 'CURRENT'
    state = ListSelection([], '', selected=-1)
    frame = list_screen_frame(state, 'AniList · Airing' if airing else 'AniList', width=columns, height=rows,
        badge='AniList', tabs=NavigationTabs(tuple(ViewTab(key, label) for key, label in tabs), active),
        status=loading_text, empty='Loading titles…',
        shortcuts=(('enter', 'open'), ('tab', 'next'), ('?', 'help'), ('esc', 'search')))
    return compose(frame, rows, columns)
