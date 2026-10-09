from media_cli.playback import mpv_runtime
from media_cli.playback.rules import (
    episode_transition_osd,
    marked_watched_osd,
    next_up_osd,
    pending_completion_osd,
    skip_prompt_osd,
    skipped_interval_osd,
    sync_queued_osd,
)


def test_playback_status_osd_is_compact():
    assert marked_watched_osd("5", synced=True) == "Watched EP 5 synced"
    assert marked_watched_osd("5", synced=False) == "Watched EP 5 saved"
    assert sync_queued_osd("5") == "Watched EP 5 sync queued"
    assert pending_completion_osd("5", "6") == "EP 5 completion pending\nAfter EP 6 plays 2 min"


def test_episode_transition_osd_is_short_and_clear():
    assert episode_transition_osd("NEXT", "loading") == "Loading next episode"
    assert episode_transition_osd("NEXT", "starting") == "Next episode ready\nStarting next episode"
    assert episode_transition_osd("NEXT", "ready") == "Next episode ready"
    assert episode_transition_osd("NEXT", "failed") == "Next episode unavailable"
    assert episode_transition_osd("PREV", "loading") == "Loading previous episode"


def test_next_up_osd_mentions_existing_shortcut():
    assert next_up_osd("Grand Blue - EP 2", 24) == "Next up\nGrand Blue - EP 2\nShift+Right now • 0:24"


def test_skip_osd_wording_is_consistent():
    assert skipped_interval_osd("Opening", "01:30", "03:00") == "Opening skipped 01:30 → 03:00"
    assert skip_prompt_osd("Opening", "01:30", "03:00") == "[Tab] Skip Opening 01:30 → 03:00"

    script = mpv_runtime.ANISKIP_LUA_SCRIPT
    assert "skipped %s (%s" not in script
    assert "[Tab/s] Skip" not in script
    assert "%s skipped %s" in script
    assert "[Tab] Skip %s %s" in script
