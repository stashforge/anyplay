"""Structured metadata shares existing decisions without passing embedded colors."""

import pytest

from media_cli.core.terminal import strip_ansi
from media_cli.domain import metadata


@pytest.fixture(autouse=True)
def online(monkeypatch):
    monkeypatch.setattr("media_cli.core.network.is_confirmed_offline", lambda: False)


def parts(show, **kwargs):
    builder = getattr(metadata, "format_info_metadata_parts", None)
    assert builder is not None, "Provide semantic metadata alongside the legacy formatter"
    return builder(show, **kwargs)


@pytest.mark.parametrize(
    "status,role",
    [
        ("CURRENT", "anilist.watching"),
        ("COMPLETED", "anilist.completed"),
        ("PLANNING", "anilist.planning"),
        ("DROPPED", "anilist.dropped"),
        ("PAUSED", "anilist.paused"),
        ("REPEATING", "anilist.rewatching"),
    ],
)
def test_anilist_status_is_role_data_not_embedded_color(status, role):
    show = {
        "_sync_enabled": True,
        "_anilist_list": status,
        "status": "FINISHED",
        "type": "TV",
        "episodeCount": 12,
        "_local_progress": 4,
        "airedStart": {"year": 2023},
        "score": 84,
    }
    result = parts(show)
    assert isinstance(result, tuple)
    assert result[0][1] == role
    assert "".join(text for text, _ in result) == strip_ansi(metadata.format_info_metadata_line(show))
    assert ("Watched", "muted") in result
    assert (" 4/12", "details") in result
    assert ("FINISHED", "metadata.status") in result
    assert all("\033" not in text for text, _ in result)


@pytest.mark.parametrize("override", [None, "7", "Movie", "Full"])
@pytest.mark.parametrize("local_only", [False, True])
def test_progress_overrides_local_totals_and_availability_keep_text_parity(override, local_only):
    show = {
        "status": "RELEASING",
        "type": "ONA",
        "episodeCount": 12,
        "originalEpisodeCount": 24,
        "_local_progress": 3,
        "availableEpisodes": {"sub": 8},
        "_next_airing_ep": 9,
        "_next_airing_at": 100000,
        "airedStart": {"year": 2026},
    }
    kwargs = dict(override_ep_str=override, local_only=local_only, now=99000)
    result = parts(show, **kwargs)
    assert "".join(text for text, _ in result) == strip_ansi(metadata.format_info_metadata_line(show, **kwargs))
    assert any(role == "metadata.airing" for _, role in result)


def test_offline_and_hidden_anilist_status_have_explicit_roles(monkeypatch):
    monkeypatch.setattr("media_cli.core.network.is_confirmed_offline", lambda: True)
    show = {"_sync_enabled": True, "_anilist_list": "CURRENT", "type": "TV"}
    result = parts(show, hide_anilist_status="CURRENT")
    assert ("OFFLINE", "metadata.offline") in result
    assert not any(role.startswith("anilist.") for _, role in result)
    assert "".join(text for text, _ in result) == strip_ansi(
        metadata.format_info_metadata_line(show, hide_anilist_status="CURRENT")
    )


def test_legacy_formatter_retains_its_exact_escape_output():
    show = {"status": "FINISHED", "type": "TV", "episodeCount": 12, "_local_progress": 4}
    assert metadata.format_info_metadata_line(show) == (
        "\033[38;5;250mFINISHED\033[38;5;248m • TV • SUB • \033[38;5;244mWatched\033[38;5;248m 4/12 • \u2605 -"
    )


def test_metadata_parts_keep_ui_types_out_of_domain_data():
    result = parts({"type": "TV"})
    assert all(isinstance(part, tuple) and len(part) == 2 for part in result)
    assert all(isinstance(text, str) and isinstance(role, str) for text, role in result)


def test_role_assignment_does_not_depend_on_legacy_escape_colors(monkeypatch):
    monkeypatch.setattr(metadata, "anilist_list_status_label", lambda *args, **kwargs: "\033[31mAL WATCHING\033[0m")
    show = {"_sync_enabled": True, "_anilist_list": "CURRENT"}
    assert parts(show)[0] == ("AL WATCHING", "anilist.watching")


@pytest.mark.parametrize("status", ["FINISHED", "AIRING", "NOT_YET_RELEASED", "CANCELLED", "HIATUS", ""])
def test_media_statuses_and_empty_total_local_mode_preserve_legacy_text(status):
    show = {"status": status, "originalEpisodeCount": 24, "type": "TV"}
    result = parts(show, local_only=True)
    assert "".join(text for text, _ in result) == strip_ansi(metadata.format_info_metadata_line(show, local_only=True))
