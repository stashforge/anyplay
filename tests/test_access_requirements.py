"""Setup failures stay distinct from network, verification and authentication errors."""

import subprocess
import sys
import time
from contextlib import contextmanager
from unittest.mock import Mock

import pytest

from media_cli.core.api import ProviderDependencyError
from media_cli.services.android_webview import client as transport
from media_cli.services.browser_access.access import AccessResponse


@pytest.mark.parametrize("desktop", [False, True])
def test_browser_prerequisites_are_typed_before_launch(monkeypatch, tmp_path, desktop):
    from media_cli.services.browser_access import process
    from media_cli.services.browser_access.policy import BrowserPolicy
    from media_cli.services.browser_access.profiles import BrowserProfile

    monkeypatch.setattr(process.sys, "platform", "linux")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    if desktop:
        monkeypatch.setenv("DISPLAY", ":1")
    monkeypatch.setattr(process.shutil, "which", lambda _: None)
    profile = tmp_path / "profile"
    profile.mkdir(mode=0o700)
    extension = profile / "helper"
    extension.mkdir(mode=0o700)
    policy = BrowserPolicy("animepahe", ["https://animepahe.pw"])
    owner = process.OwnedChromium(BrowserProfile("animepahe", profile, False), policy, extension,
                                  "https://animepahe.pw", Mock())
    launch = Mock(side_effect=AssertionError("Missing browser must not launch"))
    monkeypatch.setattr(process.subprocess, "Popen", launch)
    with pytest.raises(ProviderDependencyError, match="Chromium" if desktop else "desktop"):
        owner.__enter__()
    launch.assert_not_called()


@pytest.mark.parametrize("parent_exists", [False, True])
def test_missing_pairing_is_actionable_without_creating_credentials(tmp_path, parent_exists):
    parent = tmp_path / "companion"
    if parent_exists:
        parent.mkdir(mode=0o700)
    client = transport.ProbeClient(token_path=parent / "token")
    with pytest.raises(transport.CompanionSetupRequired, match="anyplay companion pair"):
        client._load_token()
    assert not client.token_path.exists()
    assert parent.exists() is parent_exists


@pytest.mark.parametrize("profile", ["senshi", "animepahe"])
def test_missing_capability_is_typed_and_does_not_change_pairing(monkeypatch, tmp_path, profile):
    path = tmp_path / "token"
    path.write_text("a" * 64)
    path.chmod(0o600)
    client = transport.ProbeClient(token_path=path)
    before = path.read_bytes(), path.stat().st_ino
    monkeypatch.setattr(client, "ensure_started", lambda *a: {})
    create = Mock(side_effect=AssertionError("Old companion must not create session"))
    monkeypatch.setattr(client, "create_session", create)
    with pytest.raises(transport.CompanionSetupRequired, match="[Uu]pdate"):
        with client.session(profile, deadline=time.monotonic() + 2):
            pytest.fail("Unsupported capability accepted")
    assert (path.read_bytes(), path.stat().st_ino) == before
    create.assert_not_called()


def test_missing_am_is_typed_without_exposing_wake_proof(monkeypatch):
    monkeypatch.setattr(transport.sys, "platform", "android")
    monkeypatch.setattr(transport.subprocess, "run", Mock(side_effect=FileNotFoundError("sensitive command details")))
    with pytest.raises(transport.CompanionSetupRequired, match="termux-am") as caught:
        transport.ProbeClient()._wake("a" * 64)
    assert "sensitive" not in str(caught.value)
    assert "a" * 64 not in str(caught.value)


@pytest.mark.parametrize("setup", [False, True])
def test_senshi_webview_preserves_setup_error_but_not_generic_probe_error(setup):
    from media_cli.extractors import senshi_webview
    from media_cli.extractors.senshi_runtime import SenshiRuntimeError

    # A typed setup error must not become the adapter's ordinary runtime failure.
    from media_cli.services.android_webview.client import CompanionSetupRequired

    failure = (CompanionSetupRequired("Update Anyplay WebView, then retry.") if setup
               else transport.ProbeError("Mediated request failed"))

    class Client:
        @contextmanager
        def session(self, *a, **kw):
            raise failure
            yield  # pragma: no cover

    with pytest.raises(ProviderDependencyError if setup else SenshiRuntimeError) as caught:
        senshi_webview.resolve(123, client=Client())
    if setup:
        assert str(caught.value) == str(failure)
        assert isinstance(caught.value, transport.CompanionSetupRequired)
    else:
        assert not isinstance(caught.value, ProviderDependencyError)


def test_ordinary_android_http_needs_no_companion_setup():
    from media_cli.services.android_webview.site_access import fetch_site_response

    client = Mock()
    client.session.side_effect = AssertionError("Working HTTP must not check pairing or capabilities")
    response = AccessResponse("https://animepahe.pw/api", 200, "working")
    assert fetch_site_response(response.url, lambda _: response, deadline=time.monotonic() + 2, client=client) is response
    client.session.assert_not_called()


def test_ordinary_desktop_http_needs_no_browser_setup(monkeypatch, tmp_path):
    from media_cli.services.browser_access import access
    from media_cli.services.browser_access.policy import BrowserPolicy

    owner = Mock(side_effect=AssertionError("Working HTTP must not construct browser"))
    monkeypatch.setattr(access, "OwnedChromium", owner)
    service = access.BrowserAccess(tmp_path / "profiles")
    response = AccessResponse("https://animepahe.pw/api", 200, "working")
    try:
        assert service.get(BrowserPolicy("animepahe", ["https://animepahe.pw"]), response.url,
                           lambda _: response) is response
        assert not service.profiles.root.exists()
    finally:
        service.close()
    owner.assert_not_called()


def test_insecure_pairing_is_not_classified_as_missing_setup(tmp_path):
    parent = tmp_path / "companion"
    parent.mkdir(mode=0o700)
    token = parent / "token"
    token.write_text("a" * 64)
    token.chmod(0o644)
    before = token.read_bytes(), token.stat().st_ino
    with pytest.raises(transport.ProbeError, match="private") as caught:
        transport.ProbeClient(token_path=token)._load_token()
    assert not isinstance(caught.value, ProviderDependencyError)
    assert (token.read_bytes(), token.stat().st_ino) == before


def test_companion_transport_retains_standalone_diagnostics(tmp_path):
    result = subprocess.run([sys.executable, transport.__file__, "--help"], cwd=tmp_path,
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_protected_site_adapter_translates_only_known_setup_failures():
    from media_cli.services.android_webview.site_access import fetch_site_response

    class Client:
        @contextmanager
        def session(self, *a, **kw):
            raise transport.CompanionSetupRequired("Run 'anyplay companion pair' first.")
            yield  # pragma: no cover

    response = AccessResponse("https://animepahe.pw/api", 403,
                              '<title>Just a moment...</title><script src="/cdn-cgi/challenge-platform/a"></script>')
    with pytest.raises(ProviderDependencyError, match="companion pair") as caught:
        fetch_site_response(response.url, lambda _: response, deadline=time.monotonic() + 2, client=Client())
    assert isinstance(caught.value, transport.ProbeError)


def test_animepahe_search_preserves_browser_setup_error(monkeypatch):
    from media_cli.providers.animepahe import AnimePaheProvider
    from media_cli.services.browser_access.process import BrowserSetupRequired

    failure = BrowserSetupRequired("Install Chromium, then retry.")
    monkeypatch.setattr("media_cli.services.browser_access.integration.linux_desktop", lambda: True)
    monkeypatch.setattr("media_cli.services.browser_access.integration.fetch_text", Mock(side_effect=failure))
    with pytest.raises(ProviderDependencyError) as caught:
        AnimePaheProvider().search("example")
    assert caught.value is failure


@pytest.mark.parametrize("failure,setup", [(FileNotFoundError, True), (PermissionError, True), (OSError, False)])
def test_browser_execution_error_classification(monkeypatch, tmp_path, failure, setup):
    from media_cli.services.browser_access import process
    from media_cli.services.browser_access.policy import BrowserPolicy
    from media_cli.services.browser_access.profiles import BrowserProfile

    policy = BrowserPolicy("animepahe", ["https://animepahe.pw"])
    owner = process.OwnedChromium(BrowserProfile("animepahe", tmp_path, False), policy, tmp_path,
                                  "https://animepahe.pw", Mock())
    monkeypatch.setattr(owner, "_arguments", lambda: ["chromium"])
    monkeypatch.setattr(process.subprocess, "Popen", Mock(side_effect=failure("private system details")))
    with pytest.raises(process.BrowserProcessError) as caught:
        owner.__enter__()
    assert isinstance(caught.value, ProviderDependencyError) is setup
    assert "private" not in str(caught.value)
    assert owner.closed.is_set()


def test_mediated_request_capability_guard_is_a_setup_error(monkeypatch):
    client = transport.ProbeClient()
    calls = []

    def request(*args, **kwargs):
        calls.append(args)
        return {}

    monkeypatch.setattr(client, "_request", request)
    with pytest.raises(transport.CompanionSetupRequired, match="[Uu]pdate"):
        client.run_mediated("owned", "script", lambda *a: None, time.monotonic() + 2)
    assert calls == [("GET", "/v1/health")]
