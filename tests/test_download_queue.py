from dataclasses import replace

import pytest

from media_cli.core.terminal import display_width
from media_cli.ui.download_queue import Transfer, queue_frame, snapshots, speed_bytes


def transfer(**changes):
    item = Transfer('one', 'Batch.S01E01.Opus.2.0.mkv', 'downloading', 1000,
                    10000, 10, 1000, 200, 300, True, '/tmp/episode.mkv',
                    'libtorrent', '', ('Fetching metadata', 'Downloading selected file'))
    return replace(item, **changes)


def test_queue_shows_filename_rates_eta_and_no_inspector():
    rows, _ = queue_frame((transfer(),), selected='one', width=100, height=30)
    rendered = '\n'.join(row.text for row in rows)
    assert 'Batch.S01E01.Opus.2.0.mkv' in rendered
    assert '↓ 1 KB/s' in rendered
    assert '↑ 200 B/s' in rendered
    assert 'ETA ≈' in rendered
    assert '10.0%' in rendered
    assert 'CPU' not in rendered
    assert 'Save:' not in rendered


@pytest.mark.parametrize('width,height', [(32, 16), (60, 24), (100, 40)])
def test_frame_bounds_and_empty_state(width, height):
    rows, _ = queue_frame((), width=width, height=height)
    assert len(rows) == height
    assert all(display_width(row.text) <= width - 1 for row in rows)
    assert any('No downloads queued.' in row.text for row in rows)
    assert snapshots(None) == ()


def test_unknown_queued_size_does_not_claim_overall_eta():
    rows, _ = queue_frame((transfer(), transfer(id='two', status='queued', total=0)), width=100)
    assert any('active ETA ≈' in row.text for row in rows)


def test_logs_replace_queue_and_completed_is_not_seeding():
    item = transfer(status='completed', done=10000, progress=100, speed=0)
    rows, _ = queue_frame((item,), log_item=item, width=100)
    rendered = '\n'.join(row.text for row in rows)
    assert 'Save: /tmp/episode.mkv' in rendered
    assert 'Downloading selected file' in rendered
    assert 'All   Active' not in rendered
    assert 'seeding' not in rendered
    assert speed_bytes('3.5MiB/s') == 3.5 * 1024 ** 2


def test_progress_has_green_completion_and_grey_track():
    from media_cli.ui.download_queue import progress_bar
    bar = progress_bar(20, 10)
    assert [(run.text, run.role) for run in bar.runs] == [('━━', 'success'), ('────────', 'muted')]


def test_manager_handoff_suspends_loading_and_runs_on_ui_thread(monkeypatch):
    import threading
    import curses
    from media_cli.ui import download_manager
    from media_cli.ui.transition import run_preparation
    events = []
    def wrapper(callback):
        assert threading.current_thread() is threading.main_thread()
        assert events[-1] == 'suspended'
        return 'BACK'
    monkeypatch.setattr('media_cli.ui.download_surface.queue_wrapper', wrapper)
    monkeypatch.setattr(download_manager, '_restore_terminal_input_modes', lambda: None)
    from media_cli.ui import display
    monkeypatch.setattr(display, 'enter_alt_screen', lambda: None)
    result = run_preparation(lambda: download_manager.run_download_manager(), tick=lambda: None,
                             on_ui_enter=lambda: events.append('suspended'),
                             on_ui_return=lambda: events.append('resumed'))
    assert result == 'BACK'
    assert events == ['suspended', 'resumed']


def test_shared_screen_opens_empty_manager_with_shortcut(monkeypatch):
    from contextlib import contextmanager
    from types import SimpleNamespace
    from media_cli.ui import screen_chrome, display, download_manager
    @contextmanager
    def terminal():
        yield SimpleNamespace(fileno=lambda: 0)
    keys = iter(['ALT_D', 'ESC'])
    opened = []
    monkeypatch.setattr(screen_chrome, 'borrow_terminal', terminal)
    monkeypatch.setattr(screen_chrome.os, 'get_terminal_size', lambda fd: __import__('os').terminal_size((80, 24)))
    monkeypatch.setattr(screen_chrome.select, 'select', lambda *args: ([0], [], []))
    monkeypatch.setattr(screen_chrome, 'get_key', lambda fd: next(keys))
    monkeypatch.setattr(screen_chrome, 'write_terminal', lambda text: None)
    monkeypatch.setattr(display, 'enter_alt_screen', lambda: None)
    monkeypatch.setattr(download_manager, 'run_download_manager', lambda **kw: opened.append(kw) or 'BACK')
    result = screen_chrome.run_framed_screen(lambda *args: [''] * 24, lambda key: 'back' if key == 'ESC' else None)
    assert opened == [{'standalone': False}]
    assert result == 'back'


def test_manager_matches_shared_frame_edges_and_footer():
    from media_cli.ui.screen_chrome import screen_header, shortcut_content
    rows, _ = queue_frame((), width=80, height=24)
    heading, divider = screen_header('Download Manager', width=79)
    assert rows[0] == heading
    assert rows[1] == divider
    assert rows[-2] == divider
    assert rows[-1] == shortcut_content(79, (("p", "pause/resume"), ("enter", "logs"),
                                            ("tab", "next"), ("?", "help"), ("esc", "back")))


def test_manager_shared_surface_return_preserves_unchanged_rows(monkeypatch):
    from types import SimpleNamespace
    from media_cli.ui import frame_surface, terminal_images
    from media_cli.ui.download_surface import DownloadSurface
    from media_cli.ui.list_screen import ScreenFrame
    output = []
    monkeypatch.setattr('media_cli.ui.download_surface.write_terminal', output.append)
    monkeypatch.setattr('media_cli.ui.download_surface.os.get_terminal_size', lambda fd: __import__('os').terminal_size((80, 24)))
    frame_surface.invalidate_text()
    surface = DownloadSurface(SimpleNamespace(fileno=lambda: 0))
    rows, _ = queue_frame((), width=80, height=24)
    surface.render_frame(rows)
    next_rows = list(surface.rows)
    next_rows[0] = 'Anyplay · Search'
    next_rows[3] = 'Search results'
    result = frame_surface.compose(ScreenFrame(next_rows), 24, 80)
    assert '\033[1;1H' in result
    assert '\033[4;1H' in result
    assert '\033[2;1H' not in result
    assert '\033[23;1H' not in result
    assert '\033[2J' not in result
    frame_surface.invalidate_text()


def test_tabs_use_standard_summary_row_and_shared_spacing():
    from media_cli.ui.navigation_tabs import NavigationTabs
    from media_cli.ui.view_model import ViewTab
    from media_cli.ui.download_queue import TABS
    rows, _ = queue_frame((transfer(),), width=100)
    assert rows[2].text.startswith('1 download')
    expected = NavigationTabs(tuple(ViewTab(name, name.title()) for name in TABS), 'all').content(separator='    ').text
    assert rows[2].text.endswith(expected)
    assert rows[3].text == ''


@pytest.mark.parametrize('width', [38, 60, 80, 100, 140])
def test_progress_fills_width_and_keeps_numeric_slots_stable(width):
    from media_cli.ui.download_queue import transfer_progress
    lines = [transfer_progress(percent, size, 2_000_000_000, width, speed=speed, eta=eta, indent=2)
             for percent, size, speed, eta in [(1, 999, 999, 9), (99.9, 999_000_000, 99_000_000, 999)]]
    assert all(display_width(line.text) == width for line in lines)
    assert [sum(display_width(run.text) for run in line.runs if run.role in {'success', 'muted'}) for line in lines][0] == sum(display_width(run.text) for run in lines[1].runs if run.role in {'success', 'muted'})


def test_live_queue_uses_existing_fullscreen_exit_prompt_and_separate_controls(monkeypatch):
    import curses
    import threading
    from types import SimpleNamespace
    from unittest.mock import Mock
    from media_cli.media.download_worker import DownloadItem, DownloadStatus
    from media_cli.ui.download_queue import run_queue
    item = DownloadItem('saved', 'Title', '1', '/tmp/file.mkv', status=DownloadStatus.PAUSED)
    worker = SimpleNamespace(items=[item], lock=threading.RLock(),
                             resume_item=Mock(return_value=True), cancel_item=Mock(),
                             discard_finished_item=Mock(), stop=Mock())
    screen = SimpleNamespace(render_frame=Mock(), runtime=SimpleNamespace(fileno=lambda: 7),
                             nodelay=Mock(), timeout=Mock(), leaveok=Mock(), erase=Mock())
    keys = iter([ord('p'), ord('s'), ord('x'), ord('y'), 3])
    prompt = Mock(return_value='STOP_QUIT')
    backend = SimpleNamespace(_current_terminal_size=lambda screen: (24, 100),
                              _read_input_key=lambda screen: (next(keys), False),
                              prompt_download_exit_cli=prompt,
                              show_download_exit_dialog=Mock(side_effect=AssertionError('old dialog used')))
    assert run_queue(screen, worker, backend=backend) == 'QUIT'
    worker.resume_item.assert_called_once_with('saved')
    assert worker.cancel_item.call_count == 2
    worker.discard_finished_item.assert_called_once_with('saved')
    prompt.assert_not_called()
    worker.stop.assert_not_called()


def test_numeric_details_use_inactive_tab_color_but_percentage_does_not():
    from media_cli.ui.download_queue import transfer_progress
    row = transfer_progress(12.3, 1000, 5000, 100, speed=2000)
    assert any('12.3%' in run.text and run.role == 'secondary' for run in row.runs)
    assert any('KB' in run.text and run.role == 'tab_inactive' for run in row.runs)


@pytest.mark.parametrize('keys', [['x', 'n', 'ESC'], ['p', 'DOWN', 'ESC']])
def test_remove_requires_confirmation_and_item_notice_clears_on_navigation(keys):
    import curses
    import threading
    from types import SimpleNamespace
    from unittest.mock import Mock
    from media_cli.media.download_worker import DownloadItem, DownloadStatus
    from media_cli.ui.download_queue import run_queue
    frames = []
    items = [DownloadItem(str(i), 'Title', '1', '/tmp/file'+str(i), status=DownloadStatus.PAUSED) for i in range(2)]
    worker = SimpleNamespace(items=items, lock=threading.RLock(), resume_item=Mock(return_value=False),
                             cancel_item=Mock(), discard_finished_item=Mock())
    screen = SimpleNamespace(render_frame=lambda frame: frames.append('\n'.join(row.text for row in frame)),
                             nodelay=Mock(), timeout=Mock(), leaveok=Mock())
    mapping = {'ESC': 27, 'DOWN': curses.KEY_DOWN}
    events = iter(mapping.get(key, ord(key) if len(key) == 1 else -1) for key in keys)
    backend = SimpleNamespace(_current_terminal_size=lambda screen: (24,100),
                              _read_input_key=lambda screen: (next(events),False))
    assert run_queue(screen, worker, backend=backend) == 'BACK'
    worker.cancel_item.assert_not_called()
    worker.discard_finished_item.assert_not_called()
    if keys[0] == 'x':
        assert any('Remove selected entry?' in frame for frame in frames)
        assert 'Remove selected entry?' not in frames[-1]
    else:
        assert any('Cannot resume:' in frame for frame in frames)
        assert 'Cannot resume:' not in frames[-1]


def test_unknown_total_is_omitted_and_details_right_aligned_without_moving_bar():
    from media_cli.ui.download_queue import transfer_progress
    unknown = transfer_progress(1.2, 6_800_000, 0, 100, speed=0)
    known = transfer_progress(1.2, 6_800_000, 100_000_000, 100, speed=0)
    assert '?' not in unknown.text
    assert '6.8 MB  ↓ 0.0 B/s' in unknown.text
    assert unknown.text.endswith('0.0 B/s  --')
    assert display_width(unknown.text) == display_width(known.text) == 100
    def bar_width(row):
        return sum(display_width(run.text) for run in row.runs if run.role in {'success','muted'})
    assert bar_width(unknown) == bar_width(known)


def test_manager_remembers_tab_and_selection_on_reentry():
    import curses
    import threading
    from types import SimpleNamespace
    from unittest.mock import Mock
    from media_cli.media.download_worker import DownloadItem, DownloadStatus
    from media_cli.ui.download_queue import run_queue
    worker = SimpleNamespace(lock=threading.RLock(), items=[
        DownloadItem('one', 'One', '1', '/tmp/one', status=DownloadStatus.PAUSED),
        DownloadItem('two', 'Two', '2', '/tmp/two', status=DownloadStatus.PAUSED),
    ])
    screen = SimpleNamespace(render_frame=Mock(), nodelay=Mock(), timeout=Mock(), leaveok=Mock())
    def run(keys):
        iterator = iter(keys)
        backend = SimpleNamespace(_current_terminal_size=lambda _: (24, 100),
                                  _read_input_key=lambda _: (next(iterator), False))
        return run_queue(screen, worker, backend=backend)
    assert run([9, curses.KEY_DOWN, 27]) == 'BACK'
    assert worker._queue_view_state['tab'] == 'active'
    assert worker._queue_view_state['selected'] == 'two'
    assert run([27]) == 'BACK'
    assert worker._queue_view_state['tab'] == 'active'
    assert worker._queue_view_state['selected'] == 'two'
