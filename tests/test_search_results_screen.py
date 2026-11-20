"""Shared Search results frame keeps provider records and navigation intact."""
import os

from test_ui_view_terminal import Terminal


def script(single=False):
    return f'''
from media_cli.app.search_results_controller import run_search_results
from media_cli.context import UiState
shows = [dict(_id="one", name="First title"), dict(_id="two", name="Second title")]
if {single!r}:
    shows = shows[:1]
result = run_search_results(flags, UiState(), live_fn=lambda q: ([], "", True),
    get_results=lambda: shows, get_loading=lambda: "", get_error=lambda: "",
    provider_id="example", provider_name="Example", query="test", ttype="sub",
    prompt="Search Anime", auto_single={single!r}, select=lambda show: True,
    information=lambda show: None, presentation={{"layout": "bottom"}})
finish(dict(kind=result.kind, selected=result.show.get("_id") if result.show else None))
'''


def test_search_shared_filter_selects_original_record(tmp_path):
    with Terminal(tmp_path, script()) as terminal:
        terminal.send_after("Results · test".encode(), b"/Second\r")
        assert terminal.result() == {"kind": "selected", "selected": "two"}


def test_torrent_results_use_top_order_with_bottom_preference(tmp_path):
    source = script().replace('provider_id="example"', 'provider_id="torrent"')
    with Terminal(tmp_path, source) as terminal:
        terminal.send_after('Results · test'.encode(), b'\x1b[B\r')
        assert terminal.result() == {'kind': 'selected', 'selected': 'two'}


def test_search_escape_clears_filter_then_returns_to_search(tmp_path):
    with Terminal(tmp_path, script()) as terminal:
        terminal.send_after("Results · test".encode(), b"/zzzz")
        terminal.until(b"No matching titles")
        os.write(terminal.master, b"\x1b")
        terminal.until(b"2 results")
        os.write(terminal.master, b"\x1b")
        assert terminal.result()["kind"] == "back"


def test_search_single_result_still_opens_automatically(tmp_path):
    with Terminal(tmp_path, script(single=True)) as terminal:
        assert terminal.result() == {"kind": "selected", "selected": "one"}


def test_search_live_loading_publishes_results_in_shared_frame(tmp_path):
    live_script = script().replace(
        'result = run_search_results(',
        'import time\nstarted = time.monotonic()\n'
        'def ready(): return time.monotonic() - started > 0.4\n'
        'result = run_search_results(').replace(
        'live_fn=lambda q: ([], "", True)', 'live_fn=lambda q: ([], "", ready())').replace(
        'get_results=lambda: shows, get_loading=lambda: ""',
        'get_results=lambda: shows if ready() else [], get_loading=lambda: "" if ready() else "Searching…"')
    with Terminal(tmp_path, live_script) as terminal:
        terminal.until("Searching…".encode())
        terminal.send_after(b"2 results", b"/Second\r")
        assert terminal.result() == {"kind": "selected", "selected": "two"}


def test_torrent_shared_table_and_source_summary(tmp_path):
    source = script().replace('provider_id="example"', 'provider_id="torrent"').replace(
        'if False:',
        '''for show in shows:
    show['_torrent'] = dict(source='nyaa', size_bytes=1024**3, seeders=60, leechers=2)
    show['_torrent_sources'] = {'nyaa': {'state': 'ready', 'count': 2}}
if False:''')
    with Terminal(tmp_path, source) as terminal:
        terminal.until('2 nyaa'.encode())
        terminal.until(b'prov')
        terminal.send_after(b'magnet ready', b'\r')
        assert terminal.result()['selected'] == 'one'
