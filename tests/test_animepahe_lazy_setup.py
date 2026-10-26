"""Unrelated commands must not inspect browsers for an unused provider."""

import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

from media_cli.providers import animepahe


def test_cli_import_does_not_inspect_firefox():
    code = """
import glob
import subprocess
patterns = []
commands = []
glob.glob = lambda pattern: patterns.append(pattern) or []
subprocess.check_output = lambda command, **kw: commands.append(command) or b'Firefox 147'
import media_cli.cli.main
assert not any('firefox' in str(pattern).lower() for pattern in patterns), patterns
assert not any('firefox' in str(command).lower() for command in commands), commands
"""
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr


def test_browser_setup_waits_until_first_request_and_runs_once(monkeypatch):
    # Legacy non-Linux cookie setup; desktop isolation has dedicated coverage.
    monkeypatch.setattr("media_cli.services.browser_access.integration.linux_desktop", lambda: False)
    monkeypatch.delenv("ANIMEPAHE_COOKIE", raising=False)
    monkeypatch.delenv("ANIMEPAHE_USER_AGENT", raising=False)
    cookies = Mock(return_value={"cf_clearance": "browser-cookie"})
    user_agent = Mock(return_value="Mozilla Firefox/147")
    requests = []
    session = SimpleNamespace(cookies={}, get=lambda *a, **kw: (
        requests.append(kw) or SimpleNamespace(status_code=200, text="ok")
    ))
    factory = Mock(return_value=session)
    monkeypatch.setattr(animepahe, "_find_browser_cookies", cookies)
    monkeypatch.setattr(animepahe, "_detect_firefox_user_agent", user_agent)
    monkeypatch.setattr(animepahe, "optional_curl_requests", lambda: SimpleNamespace(Session=factory))

    provider = animepahe.AnimePaheProvider()
    cookies.assert_not_called()
    user_agent.assert_not_called()
    for _ in range(2):
        assert provider._fetch_page("https://animepahe.pw/api") == "ok"
    cookies.assert_called_once_with(provider.base_url)
    user_agent.assert_called_once_with()
    factory.assert_called_once_with(impersonate="firefox147")
    assert session.cookies == {"cf_clearance": "browser-cookie"}
    assert all(call["headers"]["User-Agent"] == "Mozilla Firefox/147" for call in requests)


def test_explicit_cookie_and_user_agent_do_not_need_browser_lookup(monkeypatch):
    monkeypatch.setenv("ANIMEPAHE_COOKIE", "cf_clearance=explicit")
    monkeypatch.setenv("ANIMEPAHE_USER_AGENT", "Custom browser")
    cookies = Mock()
    user_agent = Mock()
    monkeypatch.setattr(animepahe, "_find_browser_cookies", cookies)
    monkeypatch.setattr(animepahe, "_detect_firefox_user_agent", user_agent)
    monkeypatch.setattr(animepahe, "optional_curl_requests", lambda: None)
    provider = animepahe.AnimePaheProvider()
    assert provider._get_session() is None
    assert provider._cookies == {"cf_clearance": "explicit"}
    assert provider._user_agent == "Custom browser"
    cookies.assert_not_called()
    user_agent.assert_not_called()
