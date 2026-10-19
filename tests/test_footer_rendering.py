import os
import re
from dataclasses import replace

import pytest

from media_cli.core.terminal import display_width, strip_ansi
from media_cli.ui.panels import render_search_header


def foreground_at(text, needle):
    """Inspect the actual foreground state where a visible token starts."""
    foreground = None
    plain = ""
    states = []
    position = 0
    pattern = re.compile(r"\x1b\[([0-9;]*)m")
    while position < len(text):
        match = pattern.match(text, position)
        if match:
            codes = tuple(int(value) for value in (match[1] or "0").split(";"))
            index = 0
            while index < len(codes):
                code = codes[index]
                if code in (0, 39):
                    foreground = None
                elif code == 38 and index + 2 < len(codes) and codes[index + 1] == 5:
                    foreground = (38, 5, codes[index + 2])
                    index += 2
                elif code == 38 and index + 4 < len(codes) and codes[index + 1] == 2:
                    foreground = codes[index:index + 5]
                    index += 4
                elif 30 <= code <= 37 or 90 <= code <= 97:
                    foreground = (code,)
                index += 1
            position = match.end()
        else:
            plain += text[position]
            states.append(foreground)
            position += 1
    start = plain.index(needle)
    return tuple(states[start:start + len(needle)])


def test_search_loading_reset_does_not_brighten_navigation(monkeypatch):
    monkeypatch.setattr("media_cli.ui.panels.os.get_terminal_size", lambda fd=None: os.terminal_size((200, 40)))
    show = {"_id": "1", "name": "Example", "status": "FINISHED", "_provider": "anikoto",
            "_poster_raw": "cached", "_poster_status": "ready"}
    header = render_search_header("Anikoto", "example", "sub", lambda: [show],
                                  lambda: "\033[36m* Enriching metadata...\033[0m")
    footer = header.splitlines()[-1]
    for token in ("Anikoto", "Enter=select", "Esc=quit"):
        assert set(foreground_at(footer, token)) == {(38, 5, 244)}
    assert set(foreground_at(footer, "Enriching metadata...")) == {(36,)}


def test_legacy_footer_reset_restores_base_even_without_cover():
    from media_cli.ui.poster import PosterManager
    manager = PosterManager(enabled=lambda: False, cache_dir=lambda: "/tmp",
                            hovered_show_id=lambda: None, request_redraw=lambda: None, loading_frame=lambda: "*")
    footer = manager.footer_line({}, "\033[36m* Loading\033[0m  |  Enter=open", 100)
    assert set(foreground_at(footer, "Enter=open")) == {(38, 5, 244)}


def test_footer_contract_is_immutable_and_rejects_styling():
    from media_cli.ui.footer import Footer, FooterStatus, StatusKind
    statuses = [FooterStatus("Fetching", StatusKind.LOADING)]
    context = ["Anikoto"]
    footer = Footer(statuses=statuses, context=context, actions=("Enter=open",))
    statuses.clear()
    context.clear()
    assert isinstance(footer.statuses, tuple) and len(footer.statuses) == 1
    assert footer.context == ("Anikoto",)
    with pytest.raises(ValueError, match="styling|control"):
        Footer(context=("\033[36mAnikoto\033[0m",))
    with pytest.raises(ValueError, match="styling|control"):
        FooterStatus("Fetching\nOther line", StatusKind.LOADING)


@pytest.mark.parametrize("kind", ("loading", "warning", "error", "success", "info"))
def test_semantic_status_never_changes_context_or_action_color(kind):
    from media_cli.ui.footer import Footer, FooterStatus, StatusKind, render_footer
    footer = Footer(statuses=(FooterStatus("Status", StatusKind(kind), indicator="*"),),
                    context=("Provider",), actions=("Enter=open", "Esc=back"))
    active = render_footer(footer, 120)
    idle = render_footer(replace(footer, statuses=()), 120)
    for rendered in (active, idle):
        for token in ("Provider", "Enter=open", "Esc=back"):
            assert set(foreground_at(rendered, token)) == {(38, 5, 244)}
        assert rendered.endswith("\033[0m")
    assert "Status" not in strip_ansi(idle)


@pytest.mark.parametrize("width", (1, 2, 8, 25, 80))
def test_semantic_footer_is_bounded_and_resets_after_truncation(width):
    from media_cli.ui.footer import Footer, FooterStatus, StatusKind, render_footer
    footer = Footer(statuses=(FooterStatus("Long status", StatusKind.LOADING),),
                    context=("Provider",), actions=("Enter=open",))
    rendered = render_footer(footer, width)
    assert display_width(rendered) <= max(1, width - 1)
    assert rendered.endswith("\033[0m")


def test_multiple_loading_statuses_share_one_animation_indicator():
    from media_cli.ui.footer import Footer, FooterStatus, StatusKind, render_footer
    footer = Footer(statuses=(FooterStatus("Fetching performances", StatusKind.LOADING, "~"),
                              FooterStatus("Fetching cover", StatusKind.LOADING, "~")), actions=("Esc=back",))
    line = strip_ansi(render_footer(footer, 160))
    assert line.count("~") == 1
    assert "Fetching performances" in line and "Fetching cover" in line


def test_search_accepts_semantic_loading_status_without_stringifying_it(monkeypatch):
    from media_cli.ui.footer import FooterStatus, StatusKind
    monkeypatch.setattr("media_cli.ui.panels.os.get_terminal_size", lambda fd=None: os.terminal_size((200, 40)))
    show = {"_id": "1", "name": "Example", "_poster_raw": "cached", "_poster_status": "ready"}
    status = FooterStatus("Enriching metadata", StatusKind.LOADING, "~")
    footer = render_search_header("Anikoto", "example", "sub", lambda: [show], lambda: status).splitlines()[-1]
    assert "~ Enriching metadata" in strip_ansi(footer)
    assert "FooterStatus" not in strip_ansi(footer)
    assert set(foreground_at(footer, "Enter=select")) == {(38, 5, 244)}


def test_cover_overlay_preserves_legacy_badge_color_and_dim_actions():
    from media_cli.ui.poster import PosterManager
    manager = PosterManager(enabled=lambda: True, cache_dir=lambda: "/tmp",
                            hovered_show_id=lambda: None, request_redraw=lambda: None, loading_frame=lambda: "*")
    navigation = "\033[38;2;225;85;85m[Offline]\033[0m • Enter=open"
    for state in ("ready", "loading", "failed"):
        footer = manager.footer_line({"_poster_status": state}, navigation, 160, persistent=True)
        assert set(foreground_at(footer, "[Offline]")) == {(38, 2, 225, 85, 85)}
        assert set(foreground_at(footer, "Enter=open")) == {(38, 5, 244)}


def test_failed_navigation_uses_error_role_without_changing_source_state():
    from types import SimpleNamespace
    from media_cli.ui.view_model import Blank, Content, DetailLines, Inherit, ViewItem, ViewSpec
    from media_cli.ui.view_presentation import ViewPresentation
    from media_cli.ui.view_session import BeginNavigation, FailNavigation, ViewSession
    source = ViewSpec("source", "Search", (ViewItem("artist", "Artist", details=Inherit),),
                      details=DetailLines(Content("Artist"), Blank, Blank), context=("Provider",))
    session = ViewSession(source)
    session.apply(BeginNavigation("source", session.generation, "request", "Fetching"))
    session.apply(FailNavigation("source", session.generation, "request", "Request failed"))
    manager = SimpleNamespace(enabled=lambda: False, loading_frame=lambda: "*")
    footer = ViewPresentation(session, manager).details(width=160).splitlines()[-1]
    assert session.current.state == "ready"
    assert set(foreground_at(footer, "Request failed")) == {(38, 5, 203)}
    assert set(foreground_at(footer, "Provider")) == {(38, 5, 244)}


def test_available_cover_status_preserves_sanitized_error_details():
    from media_cli.ui.footer import Footer
    from media_cli.ui.poster import PosterManager
    manager = PosterManager(enabled=lambda: True, cache_dir=lambda: "/tmp",
                            hovered_show_id=lambda: None, request_redraw=lambda: None, loading_frame=lambda: "*")
    show = {"_poster_status": "failed", "_image_message": "HTTP 403\033[31m"}
    footer = manager.footer_line(show, Footer(actions=("Enter=open",)), 160, persistent=True)
    assert "Cover failed: HTTP 403" in strip_ansi(footer)
    assert set(foreground_at(footer, "HTTP 403")) == {(38, 5, 203)}
    assert set(foreground_at(footer, "Enter=open")) == {(38, 5, 244)}


def test_explicit_footer_does_not_guess_additional_provider_context(monkeypatch):
    from media_cli.ui import display
    from media_cli.ui.footer import Footer, FooterStatus, StatusKind
    from media_cli.ui.poster import PosterManager
    manager = PosterManager(enabled=lambda: False, cache_dir=lambda: "/tmp",
                            hovered_show_id=lambda: None, request_redraw=lambda: None, loading_frame=lambda: "*")
    monkeypatch.setattr(display, "_poster_manager", manager)
    show = {"_provider": "anikoto", "_provider_name": "Anikoto", "_has_provider_link": True}
    footer = Footer(statuses=(FooterStatus("Request failed", StatusKind.ERROR),), actions=("Esc=back",))
    line = display._poster_footer_line(show, footer, 160)
    assert "Anikoto" not in strip_ansi(line)
    assert set(foreground_at(line, "Esc=back")) == {(38, 5, 244)}
