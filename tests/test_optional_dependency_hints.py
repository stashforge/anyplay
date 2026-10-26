"""Missing optional helpers report their actual impact without blocking playback."""

import sys
from copy import deepcopy
from unittest.mock import Mock

import pytest

from media_cli.providers.shared.anipm_mirrors import collect_mirrors


def base_payload():
    return {"episode": {"sourceUrls": [{"streamUrl": "https://cdn.example/master.m3u8",
                                       "sourceName": "AniPM", "resolution": "auto"}]}}


def test_missing_parser_keeps_all_adaptive_channels_with_one_hint(monkeypatch):
    from media_cli.providers.shared import anipm_mirrors
    from media_cli.providers.shared.models import normalize_episode_sources

    monkeypatch.setitem(sys.modules, "m3u8", None)
    probe = Mock(wraps=anipm_mirrors.optional_python)
    monkeypatch.setattr(anipm_mirrors, "optional_python", probe)
    base = base_payload()
    before = deepcopy(base)
    result = collect_mirrors(base, "sub", {"subHard": True},
                             resolve_channel=lambda channel: base_payload(),
                             fetch_playlist=lambda *args, **kwargs: pytest.fail("No parser: no playlist request"))
    assert len(result["episode"]["sourceUrls"]) == 2
    hints = result.get("dependency_hints", [])
    assert len(hints) == 1
    assert "m3u8" in hints[0] and "individual qualities" in hints[0]
    assert "Adaptive" in hints[0]
    assert "fail" not in hints[0] and "low success" not in hints[0]
    assert base == before
    probe.assert_called_once_with("m3u8")
    normalized = normalize_episode_sources(result, provider_id="anipm", provider_title_id="123", episode="1")
    assert normalized["dependency_hints"] == hints


def test_parser_recovery_clears_previous_missing_parser_hint(monkeypatch):
    from media_cli.services.requirements import optional_dependency_hint

    base = base_payload()
    base["dependency_hints"] = [optional_dependency_hint(
        "m3u8", fallback="Adaptive playback", feature="individual qualities")]
    result = collect_mirrors(base, "sub", {}, resolve_channel=lambda *args: None,
                             fetch_playlist=lambda *args, **kwargs: "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=100,RESOLUTION=640x360\n360.m3u8\n")
    assert "dependency_hints" not in result
    assert "360p" in result["episode"]["sourceUrls"][0]["resolution"]


def test_shared_hint_distinguishes_capability_loss_from_playback_uncertainty():
    from media_cli.services.requirements import optional_dependency_hint

    quality = optional_dependency_hint("m3u8", fallback="Adaptive playback", feature="individual qualities")
    fallback = optional_dependency_hint("bs4", fallback="Fallback playback", feature="native extraction", playback_may_fail=True)
    assert "may fail" not in quality
    assert "may fail" in fallback and "beautifulsoup4" in fallback
    assert "low success" not in quality + fallback


def test_hint_reader_is_bounded_deduplicated_and_terminal_safe():
    from media_cli.services.requirements import dependency_hint_messages

    messages = dependency_hint_messages({"dependency_hints": [None, {}, "", "\x1b[31mInstall m3u8\n", "Install m3u8", "x" * 500]})
    assert messages[0] == "Install m3u8"
    assert messages.count("Install m3u8") == 1
    assert all(len(message) <= 240 and "\x1b" not in message and "\n" not in message for message in messages)
    assert dependency_hint_messages({"dependency_hints": "bad shape"}) == ()


def test_footer_uses_warning_role_and_keeps_back_action():
    from media_cli.core.terminal import strip_ansi
    from media_cli.ui.dependency_hints import render_dependency_hint_footer

    message = "Adaptive playback; install m3u8 for individual qualities."
    footer = render_dependency_hint_footer({"dependency_hints": [message]}, 100)
    assert message in strip_ansi(footer) and "Esc=back" in strip_ansi(footer)
    assert render_dependency_hint_footer({}, 100) == ""


def test_empty_source_results_do_not_probe_optional_parser(monkeypatch):
    from media_cli.providers.shared import anipm_mirrors

    monkeypatch.setattr(anipm_mirrors, "optional_python", Mock(side_effect=AssertionError("Unused import")))
    result = collect_mirrors({"episode": {"sourceUrls": []}}, "sub", {},
                             resolve_channel=lambda *args: None, fetch_playlist=lambda *args: None)
    assert not result.get("dependency_hints")
