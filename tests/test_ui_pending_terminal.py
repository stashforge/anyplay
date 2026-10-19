import os

import pytest

from test_ui_view_terminal import Terminal, row_writes


@pytest.mark.parametrize("outcome", ("complete", "fail", "cancel"))
def test_pending_navigation_keeps_full_source_frame(tmp_path, outcome):
    gate = tmp_path / "release"
    script = f'''
from pathlib import Path
from media_cli.ui.view_model import Blank, Content, Inherit
from media_cli.ui.view_session import BeginNavigation, CompleteNavigation, FailNavigation

flags.show_image = True
rendered = {{}}
def cover(show):
    url = show.get('thumbnail')
    raw = show.get('_poster_raw') or rendered.get(url) or 'PORTRAIT-CONTENT'
    rendered[url] = raw
    show['_poster_raw'], show['_poster_status'] = raw, 'ready'
    return raw
manager.get = cover
source = ViewSpec('source', 'Search AnimeThemes',
    (ViewItem('artist', 'Miku Itou', details=Inherit),),
    details=DetailLines(Content('Artist: Miku Itou'), Content('Member of Group'), Content('ARTIST')),
    image=ImageSpec('portrait', 'available', 'https://example.test/portrait'), context=('Source context',))
destination = ViewSpec('destination', 'Select group',
    (ViewItem('group', 'Song Performances', details=Inherit),),
    details=DetailLines(Content('Artist: Miku Itou'), Content('Direct performances'), Content('48 themes')))
session = ViewSession(source)
generation = session.generation
session.apply(BeginNavigation('source', generation, 'request', 'Fetching performances'))
def complete():
    while not Path({str(gate)!r}).exists():
        time.sleep(0.01)
    if {outcome!r} == 'fail':
        session.post(FailNavigation('source', generation, 'request', 'FETCH-FAILED'))
    else:
        session.post(CompleteNavigation('source', generation, 'request', destination))
threading.Thread(target=complete, daemon=True).start()
finish(run_view_session(flags, ui, session, poster_manager=manager))
'''
    with Terminal(tmp_path, script) as terminal:
        terminal.until(b"Fetching performances")
        terminal.until(b"\033[35;100H")
        output = bytes(terminal.output)
        assert b"Select group" not in output
        assert b"Song Performances" not in output
        for content in ("Miku Itou", "Search AnimeThemes", "Artist: Miku Itou", "Member of Group", "ARTIST", "PORTRAIT-CONTENT"):
            assert content in output.decode()
        assert b"0/1" not in output
        rows = row_writes(output)
        assert any("Member of Group" in text for _, text in rows)
        if outcome == "cancel":
            os.write(terminal.master, b"\x1b")
            terminal.until(b"Source context")
        gate.touch()
        if outcome == "complete":
            terminal.until(b"48 themes")
        elif outcome == "fail":
            terminal.until(b"FETCH-FAILED")
        else:
            terminal.resize(34, 99)
            assert b"Select group" not in terminal.output
        os.write(terminal.master, b"\x03")
        assert terminal.result() is None
