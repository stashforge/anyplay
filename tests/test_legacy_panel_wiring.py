"""Legacy panels keep their layout and highlights through semantic text pieces."""

from dataclasses import replace

import pytest
from test_footer_rendering import foreground_at

from media_cli.core.terminal import strip_ansi
from media_cli.domain import metadata
from media_cli.ui import panels
from media_cli.ui.styled_text import StyledText


@pytest.fixture(autouse=True)
def deterministic_display(monkeypatch):
    monkeypatch.setattr("media_cli.core.network.is_confirmed_offline", lambda: False)
    monkeypatch.setattr(metadata.time, "time", lambda: 1000)
    monkeypatch.setattr(panels, "_poster_footer_line", lambda *args, **kwargs: "EXISTING FOOTER")


def show(status="CURRENT"):
    return dict(
        name="Example",
        englishName="Alternate",
        status="RELEASING",
        type="TV",
        episodeCount=12,
        _local_progress=4,
        _sync_enabled=True,
        _anilist_list=status,
        availableEpisodes={"sub": 8},
        _next_airing_ep=9,
        _next_airing_at=4600,
        airedStart={"year": 2026},
        score=84,
    )


@pytest.mark.parametrize("width", [1, 8, 80, 240])
@pytest.mark.parametrize("status", ["CURRENT", "COMPLETED", "PLANNING", "DROPPED", "PAUSED", "REPEATING"])
def test_legacy_panel_preserves_visible_text_and_per_piece_colors(width, status):
    item = show(status)
    old_metadata = panels.C_META + panels._t(metadata.format_info_metadata_line(item), width) + panels.C_RESET
    lines = panels.render_header_card(item, width=width, main_title="Example", footer_text="Navigation")
    assert len(lines) == 4
    assert lines[:2] == [
        panels.C_TITLE + panels._t("Example", width) + panels.C_RESET,
        panels.C_ALT + panels._t("Alternate", width) + panels.C_RESET,
    ]
    assert strip_ansi(lines[2]) == strip_ansi(old_metadata)
    assert lines[3] == "EXISTING FOOTER"
    for token in ("AL ", "AIRING", "TV", "SUB", "Watched", "4/12", "Avail", "Next", "2026", "84"):
        if token in strip_ansi(lines[2]):
            assert foreground_at(lines[2], token) == foreground_at(old_metadata, token)


def test_legacy_panel_delegates_structured_metadata_not_ansi(monkeypatch):
    shared = getattr(panels, "render_detail_lines", None)
    assert shared is not None, "Wire the legacy panel to the shared text component"
    calls = []

    def observe(**kwargs):
        calls.append(kwargs)
        return shared(**kwargs)

    monkeypatch.setattr(panels, "render_detail_lines", observe)
    panels.render_header_card(show(), width=240, main_title="Example")
    assert len(calls) == 1
    assert isinstance(calls[0]["details"], StyledText)
    assert all("\033" not in run.text for run in calls[0]["details"].runs)
    assert any(run.role == "anilist.watching" for run in calls[0]["details"].runs)


def test_offline_retains_its_distinct_red_highlight(monkeypatch):
    monkeypatch.setattr("media_cli.core.network.is_confirmed_offline", lambda: True)
    lines = panels.render_header_card(show(), width=240, main_title="Example")
    assert set(foreground_at(lines[2], "OFFLINE")) == {(38, 2, 230, 80, 80)}


@pytest.mark.parametrize("width", [1, 80])
def test_empty_selection_and_alternate_fallback_keep_compatibility(width):
    empty = panels.render_header_card(None, width=width, footer_text="Navigation")
    assert empty == ["", panels.C_HINT + "No selection" + panels.C_RESET, "", "EXISTING FOOTER"]
    item = show()
    item.pop("englishName")
    lines = panels.render_header_card(item, width=width, main_title="Example")
    assert lines[1] == panels.C_ALT + "No alternative title" + panels.C_RESET


def test_media_theme_roles_are_application_owned_and_resolvable():
    from media_cli.state.theme import APPLICATION_THEME

    for _, role in metadata.format_info_metadata_parts(show()):
        APPLICATION_THEME.resolve_style(role)
    assert APPLICATION_THEME.resolve_style("anilist.watching").foreground.value == 32


def test_shared_panel_accepts_styled_metadata_with_supplied_theme():
    from media_cli.state.theme import APPLICATION_THEME
    from media_cli.ui.detail_panel import render_detail_lines
    from media_cli.ui.styled_text import TextRun

    content = StyledText((TextRun("Watched", "muted"), TextRun(" 4/12", "details")))
    lines = render_detail_lines(
        primary="Title", secondary="Alternate", details=content, width=80, theme=APPLICATION_THEME
    )
    assert strip_ansi(lines[2]) == "Watched 4/12"
    assert set(foreground_at(lines[2], "Watched")) == {(38, 5, 244)}


def test_changing_theme_role_changes_metadata_color_without_changing_data(monkeypatch):
    from media_cli.state.theme import APPLICATION_THEME
    from media_cli.ui.theme import TextStyle

    item = show()
    before = panels.render_header_card(item, width=240, main_title="Example")
    custom = replace(
        APPLICATION_THEME,
        extensions=tuple(
            (role, TextStyle(81) if role == "anilist.watching" else style)
            for role, style in APPLICATION_THEME.extensions
        ),
    )
    monkeypatch.setattr(panels, "APPLICATION_THEME", custom)
    after = panels.render_header_card(item, width=240, main_title="Example")
    assert tuple(map(strip_ansi, after)) == tuple(map(strip_ansi, before))
    assert set(foreground_at(after[2], "AL WATCHING")) == {(38, 5, 81)}
