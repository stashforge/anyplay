import pytest

from media_cli.ui.poster import PosterManager


def manager(tmp_path, hovered):
    return PosterManager(
        enabled=lambda: True, cache_dir=lambda: str(tmp_path),
        hovered_show_id=hovered, request_redraw=lambda: None,
        loading_frame=lambda: "~",
    )


@pytest.mark.parametrize("move_away", [False, True])
def test_uncached_cover_waits_250ms_and_rechecks_selection(monkeypatch, tmp_path, move_away):
    selected = ["a"]
    sleeps = []
    fetched = []
    poster = manager(tmp_path, lambda: selected[0])
    show = {"_id": "a", "thumbnail": "https://example.test/a.png"}

    def sleep(seconds):
        sleeps.append(seconds)
        if move_away:
            selected[0] = "b"

    def fetch(url):
        fetched.append((url, tuple(sleeps)))
        # Stop before conversion/rendering; this test never performs network I/O.
        raise RuntimeError("Stub fetch stopped")

    monkeypatch.setattr("media_cli.ui.poster.time.sleep", sleep)
    monkeypatch.setattr("media_cli.ui.poster.fetch_cover_bytes", fetch)
    poster._mark_download("hash")
    poster._download(show, show["thumbnail"], "hash", str(tmp_path), str(tmp_path / "a.jpg"))
    assert sleeps == [0.25]
    assert fetched == ([] if move_away else [(show["thumbnail"], (0.25,))])
    assert "hash" not in poster.active_downloads


def test_already_rendered_cover_does_not_wait_or_start_worker(monkeypatch, tmp_path):
    poster = manager(tmp_path, lambda: "a")
    show = {"_id": "a", "thumbnail": "https://example.test/a.png", "_poster_raw": "pixels"}

    def unexpected(*args, **kwargs):
        pytest.fail("Rendered covers must not wait or start a worker")

    monkeypatch.setattr("media_cli.ui.poster.shutil.which", lambda name: "/bin/chafa")
    monkeypatch.setattr("media_cli.ui.poster.time.sleep", unexpected)
    monkeypatch.setattr("media_cli.ui.poster.threading.Thread", unexpected)
    assert poster.get(show) == "pixels"
