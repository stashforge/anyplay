"""Covers require five result slots in addition to their image/gap footprint."""

import os

import pytest
from test_ui_view_terminal import Terminal, row_writes


@pytest.mark.parametrize("termux", [False, True])
@pytest.mark.parametrize("extra_rows", [-1, 0, 1])
def test_cover_threshold_counts_five_result_rows_and_separate_gap(tmp_path, termux, extra_rows):
    cover_height = 9 if termux else 8
    rows = cover_height + 1 + 1 + 4 + 5 + extra_rows
    script = f"""
flags.show_image = True
if {termux!r}:
    os.environ['PREFIX'] = '/data/data/com.termux/files/usr'
picker_module._poster_symbol_lines = lambda raw, height, width: [f'COVER ROW {{i}}' for i in range(height)]
picker_module._poster_uses_native_protocol = lambda raw: False
finish(picker_module.tui_pick(flags, ui, 'Pick title', [f'RESULT {{i:02}}' for i in range(20)],
    top_header_fn=lambda index: 'cover', header='Primary\\nSecondary\\nDetails\\nFooter',
    return_on_quit=True))
"""
    with Terminal(tmp_path, script, rows=rows, columns=80) as terminal:
        frame = terminal.until(f"\033[{rows};80H".encode())
        writes = row_writes(frame)
        covers = [row for row, line in writes if "COVER ROW" in line]
        results = [row for row, line in writes if "RESULT" in line]
        if extra_rows < 0:
            assert covers == []
            assert len(results) >= 5
        else:
            assert len(covers) == cover_height
            assert len(results) == 5 + extra_rows
            assert min(results) - max(covers) == 2  # One separate blank row.
        os.write(terminal.master, b"\x1b")
        assert terminal.result() == -2
