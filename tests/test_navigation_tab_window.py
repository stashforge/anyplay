from media_cli.ui.navigation_tabs import NavigationTabs
from media_cli.ui.view_model import ViewTab
from media_cli.core.terminal import display_width

LABELS = ('Watching','Planning','Completed','Rewatching','Paused','Dropped','All')


def test_window_keeps_active_and_neighbors_with_hidden_edges():
    for index,label in enumerate(LABELS):
        tabs=NavigationTabs(tuple(ViewTab(value,value) for value in LABELS),label)
        value=tabs.window_content(52)
        assert display_width(value.text)<=52
        assert any(run.role=='tab_active' and run.text==label for run in value.runs)
        assert len([run for run in value.runs if run.role in ('tab_active','tab_inactive')])>=3
        assert '…' in value.text
    last=NavigationTabs(tuple(ViewTab(value,value) for value in LABELS),'All').window_content(52).text
    assert 'Dropped' in last and last.startswith('… ')


def test_long_tabs_shorten_and_tiny_terminal_is_bounded():
    tabs=NavigationTabs(tuple(ViewTab(str(i),'Very long category '+str(i)) for i in range(5)),'2')
    for width in (1,8,17,32,60,150):
        value=tabs.window_content(width)
        assert display_width(value.text)<=width
        assert any(run.role=='tab_active' for run in value.runs)
    assert len([run for run in tabs.window_content(32).runs if run.role.startswith('tab_')])>=3


def test_window_does_not_move_until_active_passes_visible_edge():
    viewport = {}
    def render(active):
        tabs=NavigationTabs(tuple(ViewTab(value,value) for value in LABELS),active)
        return tabs.window_content(52,viewport=viewport)
    render(LABELS[0])
    original=viewport['window']
    start,end=original[:2]
    for active in LABELS[start:end]:
        render(active)
        assert viewport['window']==original
    render(LABELS[end])
    shifted=viewport['window']
    assert shifted[0]>start
    render(LABELS[shifted[0]])
    assert viewport['window']==shifted
    render(LABELS[shifted[0]-1])
    assert viewport['window'][0]<shifted[0]


def test_shared_screen_rebuild_reuses_session_tab_viewport():
    from types import SimpleNamespace
    from media_cli.ui.navigation_tabs import tab_windows
    from media_cli.ui.legacy_list_picker import legacy_list_frame
    owner=SimpleNamespace()
    for active in LABELS[:2]:
        tabs=NavigationTabs(tuple(ViewTab(value,value) for value in LABELS),active)
        legacy_list_frame('AniList', ['One'],[0],0,width=70,height=30,
            chrome={'title':'AniList','tabs':tabs},tab_viewports=tab_windows(owner))
        window=next(iter(owner._tab_windows.values()))['window']
        if active==LABELS[0]: original=window
        else: assert window==original
