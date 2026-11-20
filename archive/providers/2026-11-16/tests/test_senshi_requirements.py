"""Senshi extraction tools affect remote sources, not catalog/direct alternatives."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from media_cli.core.api import ProviderDependencyError
from media_cli.extractors import senshi_runtime as runtime


@pytest.mark.parametrize("missing", ["node", "bwrap", "prlimit"])
def test_missing_linux_tool_is_typed_before_fetch(monkeypatch, missing):
    monkeypatch.setattr(runtime, "is_termux", lambda: False)
    monkeypatch.setattr(runtime.sys, "platform", "linux")
    monkeypatch.setattr("shutil.which", lambda name: None if name == missing else f"/test/{name}")
    monkeypatch.setattr(runtime.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=0, stdout="v26.1.0"))
    fetch = Mock(side_effect=AssertionError("Missing tools must not fetch runtime"))
    monkeypatch.setattr(runtime, "_fetch_runtime", fetch)
    with pytest.raises(ProviderDependencyError, match=missing):
        runtime.resolve_vidcloud(123)
    fetch.assert_not_called()


def test_old_node_reports_requirement_before_fetch(monkeypatch):
    monkeypatch.setattr(runtime, "is_termux", lambda: False)
    monkeypatch.setattr("shutil.which", lambda name: f"/test/{name}")
    monkeypatch.setattr(runtime.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=0, stdout="v24.8.0"))
    fetch = Mock(side_effect=AssertionError("Unsupported Node must not fetch runtime"))
    monkeypatch.setattr(runtime, "_fetch_runtime", fetch)
    with pytest.raises(ProviderDependencyError, match="26"):
        runtime.resolve_vidcloud(123)
    fetch.assert_not_called()


def test_termux_does_not_check_linux_tools(monkeypatch):
    monkeypatch.setattr(runtime, "is_termux", lambda: True)
    monkeypatch.setattr("shutil.which", Mock(side_effect=AssertionError("Termux uses WebView")))
    monkeypatch.setattr(runtime, "_resolve_webview", lambda *a, **kw: [{"source": "phone"}])
    assert runtime.resolve_vidcloud(123) == [{"source": "phone"}]


def test_unsupported_platform_does_not_suggest_installing_linux_tools(monkeypatch):
    monkeypatch.setattr(runtime, "is_termux", lambda: False)
    monkeypatch.setattr(runtime.sys, "platform", "darwin")
    monkeypatch.setattr("shutil.which", Mock(side_effect=AssertionError("Unsupported platform must not probe tools")))
    with pytest.raises(ProviderDependencyError, match="requires Linux"):
        runtime.resolve_vidcloud(123)


def test_unusable_node_reports_install_hint_without_raw_loader_output(monkeypatch):
    monkeypatch.setattr(runtime, "is_termux", lambda: False)
    monkeypatch.setattr(runtime.sys, "platform", "linux")
    monkeypatch.setattr("shutil.which", lambda name: f"/test/{name}")
    monkeypatch.setattr(runtime.subprocess, "run", Mock(side_effect=OSError("private loader details")))
    with pytest.raises(ProviderDependencyError, match="Install a working Node.js 26") as caught:
        runtime.resolve_vidcloud(123)
    assert "private" not in str(caught.value)


def test_validated_runtime_preserves_existing_native_route(monkeypatch):
    monkeypatch.setattr(runtime, "is_termux", lambda: False)
    monkeypatch.setattr(runtime.sys, "platform", "linux")
    monkeypatch.setattr("shutil.which", lambda name: f"/test/{name}")
    monkeypatch.setattr(runtime.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=0, stdout="v26.1.0"))
    monkeypatch.setattr(runtime, "require_python", lambda *a: SimpleNamespace(Session=lambda **kw: Session()))
    calls = []

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    monkeypatch.setattr(runtime, "_fetch_runtime", lambda *args: calls.append("fetch") or "runtime")
    monkeypatch.setattr(runtime, "_run", lambda *args: calls.append("run") or [{"source": "native"}])
    assert runtime.resolve_vidcloud(123) == [{"source": "native"}]
    assert calls == ["fetch", "run"]


@pytest.mark.parametrize("alternative", [False, True])
def test_provider_preserves_runtime_requirement_or_other_sources(monkeypatch, alternative):
    from media_cli.providers.senshi import Senshi
    from media_cli.services.requirements import MissingExecutable

    provider = Senshi()
    failure = MissingExecutable("node", "Senshi remote extraction")
    monkeypatch.setattr("media_cli.services.requirements.require_provider_operation", lambda *a: None)
    remote = Mock(side_effect=failure)
    monkeypatch.setattr(runtime, "resolve_vidcloud", remote)
    embeds = [{"remote_source_id": 123, "status": "HardSub"}]
    if alternative:
        embeds.append({"url": "https://cdn.example/video.m3u8", "status": "HardSub"})
    monkeypatch.setattr(provider, "_fetch_json", lambda *a: embeds)
    if alternative:
        response = provider.episode_sources("show", "1")
        assert len(response["episode"]["sourceUrls"]) == 1
        assert "node" in response["dependency_hints"][0]
        assert "Install" in response["dependency_hints"][0]
    else:
        with pytest.raises(ProviderDependencyError) as caught:
            provider.episode_sources("show", "1")
        assert caught.value is failure
        assert not provider._remote_sources_cache
        assert not provider._sources_cache


def test_source_advisory_survives_shared_resolution(monkeypatch):
    from media_cli.media import resolver

    monkeypatch.setattr(resolver, "_resolve_source_impl", lambda *a, **kw: [{"link": "https://cdn.example/video.mp4"}])
    result = resolver.resolve_source({"sourceName": "Direct", "dependency_hints": ["Install Node.js for remote extraction."]})
    assert result[0]["dependency_hints"] == ["Install Node.js for remote extraction."]
