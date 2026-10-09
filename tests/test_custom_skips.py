import textwrap

from media_cli.media import custom_skips
from media_cli.state import preferences


def test_parse_custom_skip_form_defaults_and_times():
    spec = custom_skips.parse_skip_form(
        textwrap.dedent(
            """
            # comments are ignored
            providers = all
            exclude_providers = torrents-csv
            mode = session
            opening = 1:30
            ending = 22:10
            """
        )
    )

    assert spec["mode"] == "session"
    assert spec["providers"] == ["all"]
    assert spec["exclude_providers"] == ["torrents-csv"]
    assert spec["intervals"] == [
        {"type": "op", "label": "Opening", "start": 0.0, "end": 90.0},
        {"type": "ed", "label": "Ending", "start": 1330.0, "end": None},
    ]


def test_parse_custom_skip_form_rejects_bad_ranges():
    try:
        custom_skips.parse_skip_form("opening = 1:00 0:30\n")
    except custom_skips.SkipFormError as exc:
        assert "opening" in str(exc).lower()
        assert "end" in str(exc).lower()
    else:
        raise AssertionError("expected invalid range to fail")


def test_custom_skip_provider_matching_prefers_provider_specific():
    title_entries = [
        {
            "providers": ["all"],
            "exclude_providers": ["torrents-csv"],
            "intervals": [{"type": "op", "label": "Opening", "start": 0.0, "end": 90.0}],
        },
        {
            "providers": ["mkissa"],
            "exclude_providers": [],
            "intervals": [{"type": "op", "label": "Opening", "start": 12.0, "end": 92.0}],
        },
    ]

    assert custom_skips.pick_provider_entry(title_entries, "mkissa")["intervals"][0]["start"] == 12.0
    assert custom_skips.pick_provider_entry(title_entries, "anizone")["intervals"][0]["end"] == 90.0
    assert custom_skips.pick_provider_entry(title_entries, "torrents-csv") is None


def test_template_regenerates_comments_and_provider_list():
    template = custom_skips.render_skip_form(
        title="Demo",
        providers=["mkissa", "anizone", "torrent"],
        entry={
            "mode": "title",
            "providers": ["all"],
            "exclude_providers": ["torrents-csv"],
            "intervals": [{"type": "op", "label": "Opening", "start": 0.0, "end": 120.0}],
        },
    )

    assert "# Available providers: mkissa, anizone, torrent" in template
    assert "mode = title" in template
    assert "providers = all" in template
    assert "exclude_providers = torrents-csv" in template
    assert "opening = 2:00" in template


def test_preferences_store_custom_skips_under_all_title_keys():
    prefs = {}
    show = {"_id": "provider-1", "aniListId": 123, "name": "Demo"}
    entry = {
        "providers": ["all"],
        "exclude_providers": ["torrents-csv"],
        "intervals": [{"type": "op", "label": "Opening", "start": 0.0, "end": 90.0}],
    }

    preferences.set_custom_skips(prefs, show, "provider-1", entry)

    assert preferences.custom_skips(prefs, show, "provider-1") == [entry]
    assert preferences.custom_skips(prefs, {"aniListId": 123}, None) == [entry]


def test_resolve_custom_skips_prefers_session_over_saved():
    show = {"_id": "provider-1", "name": "Demo"}
    saved = {
        "providers": ["all"],
        "exclude_providers": [],
        "intervals": [{"type": "op", "label": "Opening", "start": 0.0, "end": 90.0}],
    }
    session = {
        "providers": ["all"],
        "exclude_providers": [],
        "intervals": [{"type": "op", "label": "Opening", "start": 12.0, "end": 92.0}],
    }

    resolved = custom_skips.resolve_custom_skips(
        show=show,
        fallback_show_id="provider-1",
        provider_id="mkissa",
        session_store={"provider-1": [session]},
        saved_entries=[saved],
    )

    assert resolved["mode"] == "session"
    assert resolved["intervals"][0]["start"] == 12.0


def test_desktop_playback_custom_skips_override_aniskip(monkeypatch):
    from media_cli.playback import desktop

    loaded = {}

    class FakeIpc:
        props = {"percent-pos": 0, "playback-time": 0, "duration": 0}

        def load(self, url, title, headers, referer, start_time, osd_msg, audio_url, subtitle_url, **kwargs):
            loaded["skip_intervals"] = kwargs["skip_intervals"]

        def wait_for_playback(self, *args, **kwargs):
            return "QUIT", 0

    def fail_fetch(*args, **kwargs):
        raise AssertionError("AniSkip should not be fetched when custom skips exist")

    monkeypatch.setattr("media_cli.media.aniskip.fetch_skip_times", fail_fetch)

    desktop.play_desktop(
        FakeIpc(),
        "Demo",
        "1",
        {"link": "/tmp/demo.mp4", "is_local": True, "resolution": "Local"},
        get_resume_time=lambda *a: 0,
        get_preferred_mirror=lambda *a: {},
        update_stream_info=lambda *a: None,
        mal_id=123,
        aniskip_enabled=True,
        custom_skip_intervals=[{"type": "op", "label": "Opening", "start": 0.0, "end": 90.0}],
    )

    assert loaded["skip_intervals"] == [{"type": "op", "label": "Opening", "start": 0.0, "end": 90.0}]
