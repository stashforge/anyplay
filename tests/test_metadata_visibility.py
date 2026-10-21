"""Availability visibility is a domain policy, not a per-screen decision."""

from copy import deepcopy

import pytest

from media_cli.app.show_view_data import build_show_detail_lines
from media_cli.core.terminal import strip_ansi
from media_cli.domain.metadata import format_available_episodes, format_info_metadata_line


@pytest.mark.parametrize("watched,available,total,expected", [
    (13, 13, None, ""),
    (12, 13, None, "Avail 13"),
    (13, 13, 24, ""),
    (12, None, 24, ""),
    (None, None, None, ""),
    (12, 13, 13, ""),
    (3, 2, 24, "Avail 2"),
])
def test_list_and_details_share_availability_visibility(watched, available, total, expected):
    show = dict(_id="one", name="Example", status="RELEASING", type="TV",
                _local_progress=watched, _local_episode_label=watched)
    if available is not None:
        show["availableEpisodes"] = {"sub": available}
    if total is not None:
        show["episodeCount"] = total
    for local_only in (True, False):
        assert format_available_episodes(show, local_only=local_only) == expected
        lines = build_show_detail_lines(deepcopy(show), "sub", local_only=local_only)
        text = lines.metadata.text
        assert ("Avail" in text) == bool(expected)
        assert text == strip_ansi(format_info_metadata_line(show, local_only=local_only))


def test_equal_counts_keep_meaningful_absolute_episode_number():
    show = dict(_id="one", name="Example", status="RELEASING", _local_progress=32,
                _local_episode_label="Episode 32 [464]", availableEpisodes={"sub": 32},
                _episode_ids=["first", "last"],
                _episode_labels={"first": "Episode 1 [433]", "last": "Episode 32 [464]"})
    for local_only in (True, False):
        assert format_available_episodes(show, local_only=local_only) == ""
        text = build_show_detail_lines(show, "sub", local_only=local_only).metadata.text
        assert "Watched 32 [464]" in text and "Avail" not in text


@pytest.mark.parametrize("total", [None, 52])
def test_behind_keeps_watched_and_available_absolute_numbers(total):
    show = dict(name="Example", status="RELEASING", _local_progress=31,
                _local_episode_label="Episode 31 [463]", episodeCount=total,
                availableEpisodes={"sub": 32},
                _episode_display={"sub": {"available": "32", "latest_label": "Episode 32 [464]"}})
    for local_only in (True, False):
        text = build_show_detail_lines(show, "sub", local_only=local_only).metadata.text
        watched = "Watched 31/52 [463]" if total else "Watched 31 [463]"
        assert watched in text and "Avail 32 [464]" in text


def test_unequal_unknown_total_does_not_disappear_only_in_history():
    show = dict(_id="one", name="Example", status="RELEASING", _local_progress=12,
                _local_episode_label=12, availableEpisodes={"sub": 13})
    listed = build_show_detail_lines(show, "sub", local_only=True).metadata.text
    detailed = build_show_detail_lines(show, "sub", local_only=False).metadata.text
    assert listed == detailed
    assert "Watched 12" in listed and "Avail 13" in listed


@pytest.mark.parametrize("airing_at", [50, 150])
def test_airing_time_availability_uses_same_clock_in_both_views(monkeypatch, airing_at):
    monkeypatch.setattr("media_cli.domain.metadata.time.time", lambda: 100)
    show = dict(status="RELEASING", _local_progress=10, _local_episode_label=10,
                _next_airing_ep=13, _next_airing_at=airing_at)
    expected = "Avail 13" if airing_at == 50 else "Avail 12"
    assert format_available_episodes(show, local_only=True) == expected
    assert format_available_episodes(show, local_only=False) == expected
