from media_cli.core.terminal import strip_ansi
from media_cli.domain.metadata import format_available_episodes, format_progress


def _show(*, total=None, available=21):
    show = {
        "status": "RELEASING",
        "availableEpisodes": {"sub": available},
        "_episode_ids": [f"episode-{number}" for number in range(433, 454)],
        "_episode_labels": {
            f"episode-{number}": f"Episode {number - 432} [{number}]"
            for number in range(433, 454)
        },
        "_local_progress": "451",
        "_local_episode_label": "Episode 19 [451]",
    }
    if total is not None:
        show["episodeCount"] = total
    return show


def test_format_progress_uses_primary_number_for_dual_episode_label():
    rendered = format_progress(_show(total=52), local_only=True, ttype="sub")

    assert strip_ansi(rendered) == "Watched 19/52"
    assert strip_ansi(format_available_episodes(_show(total=52), ttype="sub")) == "Avail 21 [453]"


def test_format_progress_omits_total_when_season_total_is_unknown():
    rendered = format_progress(_show(), local_only=True, ttype="sub")

    assert strip_ansi(rendered) == "Watched 19"
    assert strip_ansi(format_available_episodes(_show(), ttype="sub")) == "Avail 21 [453]"


def test_format_progress_omits_stale_total_below_available_count():
    show = _show(total=17)
    rendered = format_progress(show, local_only=True, ttype="sub")

    assert strip_ansi(rendered) == "Watched 19"
    assert strip_ansi(format_available_episodes(show, local_only=True, ttype="sub")) == "Avail 21 [453]"


def test_format_progress_does_not_use_available_episode_as_total():
    show = {
        "status": "RELEASING",
        "type": "ONA",
        "availableEpisodes": {"sub": 698},
        "_local_progress": "670",
        "_local_episode_label": "670",
    }

    assert strip_ansi(format_progress(show, local_only=True, ttype="sub")) == "Watched 670"


def test_format_progress_does_not_treat_donghua_latest_as_total():
    show = {
        "status": "RELEASING",
        "type": "ONA",
        "_provider": "animexin",
        "episodeCount": 698,
        "availableEpisodes": {"sub": 698},
        "_local_progress": "670",
        "_local_episode_label": "670",
    }

    assert strip_ansi(format_progress(show, local_only=True, ttype="sub")) == "Watched 670"
    assert strip_ansi(format_available_episodes(show, ttype="sub")) == "Avail 698"


def test_format_progress_uses_known_total_and_keeps_available_separate():
    show = {
        "status": "RELEASING",
        "type": "TV",
        "episodeCount": 24,
        "availableEpisodes": {"sub": 10},
        "_local_progress": "2",
        "_local_episode_label": "Episode 2",
    }

    assert strip_ansi(format_progress(show, local_only=True, ttype="sub")) == "Watched 2/24"
    assert strip_ansi(format_available_episodes(show, ttype="sub")) == "Avail 10"
