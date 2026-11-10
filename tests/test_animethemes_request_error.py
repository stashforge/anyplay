import json
from types import SimpleNamespace

from media_cli.app import animethemes


def test_invalid_json_returns_to_details_with_footer(monkeypatch):
    show = {"name": "Example"}
    feedback = []
    def loading(*args):
        raise json.JSONDecodeError("Expecting value", "", 0)
    monkeypatch.setattr(animethemes.app_core, "with_loading", loading)
    monkeypatch.setattr(animethemes.app_core, "set_action_feedback", lambda s, msg: feedback.append((s, msg)))
    assert animethemes.open_anime_songs(None, SimpleNamespace(), None, {}, None, show) == "DETAILS"
    assert feedback == [(show, "AnimeThemes returned an invalid response.")]


def test_http_status_is_checked_before_json_decode(monkeypatch):
    import urllib.error
    import pytest
    from media_cli.services import http

    monkeypatch.setattr(http._GLOBAL_POOL, "request", lambda *a, **kw: (503, {}, b"<html>Unavailable</html>"))
    with pytest.raises(urllib.error.HTTPError) as failure:
        http.request_json("https://api.animethemes.moe/anime")
    assert failure.value.code == 503
