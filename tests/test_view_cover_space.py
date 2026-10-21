"""An absent/disabled image must not reserve a blank cover area."""

import os

import pytest
from test_ui_view_terminal import Terminal, row_writes


@pytest.mark.parametrize("image,has_slot", [
    ("None", False), ("ImageSpec('cover', 'unavailable')", False), ("ImageSpec('cover', 'failed')", False),
    ("ImageSpec('cover', 'resolving')", True),
    ("ImageSpec('cover', 'available', 'https://images.test/cover.png')", True),
])
def test_cover_space_follows_explicit_image_state(tmp_path, image, has_slot):
    source = f"""
flags.show_image = True
manager.get = lambda show: ''
items = tuple(ViewItem(str(i), f'Row {{i}}', details=Inherit) for i in range(60))
view = ViewSpec('example', 'Choose', items, image={image},
    details=DetailLines(Content('TITLE'), Content('SECONDARY'), Content('METADATA')),
    context=('FOOTER-MARKER',))
session = ViewSession(view, lambda event: ExitSession(True) if event.kind == 'back' else None)
run_view_session(flags, ui, session, poster_manager=manager)
finish(dict(done=True))
"""
    with Terminal(tmp_path, source) as terminal:
        terminal.until(b"FOOTER-MARKER")
        rows = {row: text for row, text in row_writes(bytes(terminal.output)) if "Row " in text}
        assert min(rows) == (10 if has_slot else 1)
        assert len(rows) == (21 if has_slot else 30)
        os.write(terminal.master, b"\x1b")
        assert terminal.result() == {"done": True}
