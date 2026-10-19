"""Row-type spacing travels from supplied views to terminal and plain pickers."""

import os
from inspect import Parameter, signature
from types import SimpleNamespace

import pytest
from test_ui_view_terminal import Terminal, row_writes

from media_cli.ui.plain_picker import plain_pick
from media_cli.ui.row_layout import DEFAULT_ROW_INDENTATION, RowIndentation
from media_cli.ui.view_model import Blank, DetailLines, ViewSpec


def test_view_accepts_validated_indentation_and_rejects_wrong_objects():
    layout = RowIndentation(option=1, disabled=4, heading=2)
    view = ViewSpec("id", "Prompt", details=DetailLines(Blank, Blank, Blank), row_indentation=layout)
    assert view.row_indentation is layout
    assert signature(ViewSpec).parameters["row_indentation"].kind is Parameter.KEYWORD_ONLY
    with pytest.raises(TypeError, match="row_indentation"):
        ViewSpec("id", "Prompt", details=DetailLines(Blank, Blank, Blank), row_indentation={"option": 1})


@pytest.mark.parametrize("layout", [DEFAULT_ROW_INDENTATION, RowIndentation(option=1, disabled=4, heading=2)])
def test_plain_rows_apply_type_spacing_without_changing_numbering(layout):
    output = []
    result = plain_pick(
        SimpleNamespace(),
        SimpleNamespace(),
        "Prompt",
        ["Heading", "Blocked", "Option"],
        heading_indices={0},
        disabled_indices={1},
        row_indentation=layout,
        input_fn=lambda _: "1",
        output_fn=output.append,
    )
    from media_cli.core.terminal import strip_ansi

    clean = tuple(strip_ansi(line) for line in output)
    assert " " * layout.heading + "Heading" in clean
    assert " " * layout.disabled + "  Blocked (disabled)" in clean
    assert " " * layout.option + "[1] Option" in clean
    assert result == 2


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("custom", [False, True])
def test_structured_rows_use_type_spacing_and_keep_selection(tmp_path, reverse, custom):
    values = dict(option=1, disabled=4, heading=2) if custom else dict(option=0, disabled=0, heading=0)
    script = f"""
from media_cli.ui.row_layout import RowIndentation
view = ViewSpec('root', 'ROW-SPACING', (
    ViewItem('heading', 'Heading', kind='heading', details=Inherit),
    ViewItem('disabled', 'Blocked', enabled=False, details=Inherit),
    ViewItem('a', 'Option A', details=Inherit),
    ViewItem('b', 'Option B', details=Inherit),
), details=DetailLines(Content('DETAIL READY'), Blank, Blank), reverse_items={reverse!r},
row_indentation=RowIndentation(**{values!r}))
session = ViewSession(view, lambda event: ExitSession(event.item_id) if event.kind == 'select' else None)
finish(run_view_session(flags, ui, session, poster_manager=manager))
"""
    with Terminal(tmp_path, script) as terminal:
        terminal.until(b"\x1b[35;100H")
        rows = dict(row_writes(bytes(terminal.output)))
        for label, indent in (("Heading", values["heading"]), ("Blocked", values["disabled"])):
            line = next(text for text in rows.values() if label in text)
            assert line == " " * (indent + 2) + label
        selected = next(text for text in rows.values() if "Option A" in text)
        assert selected == " " * values["option"] + "\u276f Option A"
        os.write(terminal.master, b"\r")
        assert terminal.result() == "a"


def test_excessive_indentation_is_bounded_before_allocating_spaces(tmp_path):
    script = """
from media_cli.ui.row_layout import RowIndentation
finish(picker_module.tui_pick(flags, ui, 'BOUNDED-SPACING', ['Option'],
    row_indentation=RowIndentation(option=10**12, disabled=0, heading=0),
    header='DETAIL READY', return_on_quit=True))
"""
    with Terminal(tmp_path, script, columns=20) as terminal:
        terminal.send_after(b"\x1b[35;20H", b"\r")
        assert terminal.result() == 0
        assert len(terminal.output) < 10000
