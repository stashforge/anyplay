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
