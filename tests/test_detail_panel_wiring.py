"""Wire shared text pieces without replacing cover/footer coordination."""

import pytest
from test_ui_view_presentation import setup

from media_cli.core.terminal import sanitize_terminal_text, truncate_display
from media_cli.ui import detail_panel, view_presentation
from media_cli.ui.view_model import Content, DetailLines, ImageSpec


@pytest.mark.parametrize("width", [1, 8, 80])
@pytest.mark.parametrize("image_state", [None, "resolving", "available", "failed"])
def test_structured_presenter_uses_shared_lines_with_exact_existing_output(monkeypatch, width, image_state):
    image = (
        None
        if image_state is None
        else ImageSpec("cover", image_state, "https://example.test/cover" if image_state == "available" else None)
    )
    values = ("Primary \u754c" * 4, "Secondary\nline", "Details\033[31m text")
    presenter, _, manager = setup(image, details=DetailLines(*(Content(value) for value in values)))
    before = presenter.details("a", width)
    shared = getattr(detail_panel, "render_detail_lines", None)
    assert shared is not None, "Expose shared text pieces for the existing presenter"
    calls = []

    def observe(**kwargs):
        calls.append(kwargs)
        return shared(**kwargs)

    monkeypatch.setattr(view_presentation, "render_detail_lines", observe, raising=False)
    after = presenter.details("a", width)
    assert after == before
    assert len(calls) == 1
    assert calls[0] == dict(primary=values[0], secondary=values[1], details=values[2], width=width)
    expected = tuple(
        f"{color}{truncate_display(sanitize_terminal_text(value), max(1, width - 1))}\033[0m"
        for color, value in zip(("\033[1;97m", "\033[38;5;248m", "\033[38;5;248m"), values, strict=True)
    )
    assert tuple(after.split("\n")[:3]) == expected
    assert bool(manager.footer_calls) is (image_state == "available")
