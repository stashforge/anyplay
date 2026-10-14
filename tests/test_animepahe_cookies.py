import sqlite3

from media_cli.providers import animepahe


def _write_firefox_cookies(path):
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "CREATE TABLE moz_cookies ("
            "host TEXT, name TEXT, value TEXT, expiry INTEGER, isSecure INTEGER"
            ")"
        )
        conn.executemany(
            "INSERT INTO moz_cookies VALUES (?, ?, ?, ?, ?)",
            [
                (".animepahe.pw", "cf_clearance", "animepahe-clearance", 2_000_000_000, 1),
                ("animepahe.pw", "aud", "animepahe-aud", 2_000_000_000, 0),
                (".kwik.cx", "cf_clearance", "kwik-clearance", 2_000_000_000, 1),
                (".kwik.cx", "session", "kwik-session", 2_000_000_000, 1),
            ],
        )
        conn.commit()
    finally:
        conn.close()


def test_browser_cookie_lookup_is_domain_aware(monkeypatch, tmp_path):
    cookie_db = tmp_path / "cookies.sqlite"
    _write_firefox_cookies(cookie_db)
    monkeypatch.setattr(animepahe.glob, "glob", lambda _pattern: [str(cookie_db)])

    assert animepahe._find_browser_cookies("https://animepahe.pw/api?m=search") == {
        "cf_clearance": "animepahe-clearance",
        "aud": "animepahe-aud",
    }
    assert animepahe._find_browser_cookies("https://kwik.cx/e/example") == {
        "cf_clearance": "kwik-clearance",
        "session": "kwik-session",
    }


def test_animepahe_uses_browser_response_when_cookie_retry_is_not_enough(monkeypatch):
    class FakeCookies(dict):
        def update(self, values):
            super().update(values)

    class FakeResponse:
        def __init__(self, status_code, text):
            self.status_code = status_code
            self.text = text

    class FakeSession:
        def __init__(self):
            self.cookies = FakeCookies()
            self.calls = 0

        def get(self, *_args, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                return FakeResponse(403, "<title>Just a moment...</title>")
            return FakeResponse(200, "ok")

    provider = animepahe.AnimePaheProvider()
    provider._session = FakeSession()
    monkeypatch.setattr(
        animepahe,
        "_resolve_browser_response",
        lambda _url, _ua, timeout=25: ("browser ok", {"cf_clearance": "fresh"}),
    )

    assert provider._fetch_page("https://animepahe.pw/api?m=search&q=grand") == "browser ok"
    assert provider._session.calls == 1
    assert provider._session.cookies["cf_clearance"] == "fresh"


def test_animepahe_treats_http_200_security_page_as_cloudflare(monkeypatch):
    class FakeCookies(dict):
        def update(self, values):
            super().update(values)

    class FakeResponse:
        status_code = 200
        text = "animepahe.pw Performing security verification Ray ID: abc"

    class FakeSession:
        def __init__(self):
            self.cookies = FakeCookies()

        def get(self, *_args, **_kwargs):
            return FakeResponse()

    provider = animepahe.AnimePaheProvider()
    provider._session = FakeSession()
    monkeypatch.setattr(
        animepahe,
        "_resolve_browser_response",
        lambda _url, _ua, timeout=25: ('{"data":[]}', {"cf_clearance": "fresh"}),
    )

    assert provider._fetch_page("https://animepahe.pw/api?m=search&q=grand") == '{"data":[]}'
