"""Optional transports stay unloaded; required dependencies fail explicitly."""

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("code", [
    "import media_cli.cli.application",
    "from media_cli.providers import get_provider; get_provider('animepahe'); get_provider('reanime')",
    "from media_cli.extractors import find_extractor; find_extractor('https://dai.ly/x123')",
])
def test_unused_curl_is_not_imported_even_when_installed(tmp_path, code):
    script = """
import importlib.abc, sys
attempted = []
class Guard(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'curl_cffi' or fullname.startswith('curl_cffi.'):
            attempted.append(fullname)
            raise ModuleNotFoundError('curl blocked', name=fullname)
sys.meta_path.insert(0, Guard())
""" + code + "\nassert not attempted, attempted\n"
    result = subprocess.run([sys.executable, "-c", script], cwd=ROOT,
                            env={**os.environ, "HOME": str(tmp_path)},
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr


def requirements():
    from media_cli.services import requirements as module
    return module


@pytest.mark.parametrize("error", [ModuleNotFoundError("missing"), OSError("native loader failed")])
def test_required_import_has_actionable_diagnostics_without_raw_import_error(monkeypatch, error):
    from media_cli.core.api import ProviderDependencyError

    module = requirements()
    monkeypatch.setattr(module.importlib, "import_module", Mock(side_effect=error))
    with pytest.raises(ProviderDependencyError) as caught:
        module.require_python("curl_cffi.requests", "Miruro search")
    message = str(caught.value)
    assert "Miruro search" in message and "curl-cffi" in message
    assert "pipx inject anyplay curl-cffi" in message
    assert str(error) not in message


def test_optional_import_failure_does_not_block_supported_fallback(monkeypatch):
    module = requirements()
    importer = Mock(side_effect=[ImportError("missing"), SimpleNamespace(ok=True)])
    monkeypatch.setattr(module.importlib, "import_module", importer)
    assert module.optional_curl_requests() is None
    assert module.optional_curl_requests().ok  # No permanent negative cache.


def test_alternative_backend_accepts_one_working_library(monkeypatch):
    module = requirements()
    working = SimpleNamespace(ok=True)
    monkeypatch.setattr(module.importlib, "import_module", Mock(side_effect=[ImportError(), working]))
    assert module.require_any_python(("cryptography", "Crypto.Cipher.AES"), "Mkissa search") is working


def test_unexpected_import_bug_is_not_hidden_as_optional_unavailability(monkeypatch):
    module = requirements()
    monkeypatch.setattr(module.importlib, "import_module", Mock(side_effect=ValueError("bug")))
    with pytest.raises(ValueError, match="bug"):
        module.optional_curl_requests()


def test_anipm_mirrors_remain_available_without_quality_parser(monkeypatch):
    module = requirements()
    importer = Mock(side_effect=AssertionError("Optional quality parser must not block Mirrors"))
    monkeypatch.setattr(module.importlib, "import_module", importer)
    module.require_provider_operation("anipm", "mirrors")
    importer.assert_not_called()


@pytest.mark.parametrize("operation", ["search", "title", "episodes"])
def test_anipm_non_stream_operations_do_not_require_hls_parser(monkeypatch, operation):
    module = requirements()
    importer = Mock(side_effect=AssertionError("Must not load m3u8"))
    monkeypatch.setattr(module.importlib, "import_module", importer)
    module.require_provider_operation("anipm", operation)
    importer.assert_not_called()


@pytest.mark.parametrize("operation", ["search", "title", "sources"])
def test_service_preserves_dependency_error_instead_of_empty_result(monkeypatch, operation):
    from media_cli.core.api import ProviderDependencyError
    from media_cli.services import catalog

    failure = ProviderDependencyError("Needs a library")
    monkeypatch.setattr(catalog, "get_provider", Mock(side_effect=failure))
    monkeypatch.setattr("media_cli.ui.display.fatal_terminal_exit", Mock(side_effect=SystemExit("Unexpected worker exit")))
    with pytest.raises(ProviderDependencyError) as caught:
        if operation == "search":
            catalog.search_anime("example", provider_id="anipm")
        elif operation == "title":
            catalog.get_show_by_id("123", provider_id="anipm")
        else:
            catalog.get_episode_data("123", "1", provider_id="anipm")
    assert caught.value is failure


def test_cli_dependency_error_cleans_up_before_printing_and_returns_failure(monkeypatch, capsys):
    from media_cli.cli import application
    from media_cli.core.api import ProviderDependencyError

    events = []
    monkeypatch.setenv("ALLMANGA_NO_OS_EXIT", "1")
    monkeypatch.setattr(application.sys, "argv", ["anyplay", "--incognito"])
    monkeypatch.setattr(application.app, "app_main", Mock(side_effect=ProviderDependencyError("Miruro needs curl-cffi")))
    monkeypatch.setattr(application.app, "fatal_terminal_exit", Mock(side_effect=AssertionError("Must clean up first")))
    monkeypatch.setattr(application.app, "restore_terminal", lambda: events.append("restore"))
    monkeypatch.setattr(application, "_cleanup", lambda: events.append("cleanup"))
    assert application.run() == 1
    assert events == ["cleanup", "restore"]
    assert "Miruro needs curl-cffi" in capsys.readouterr().err


@pytest.mark.parametrize("reader", ["error", "results", "live"])
def test_late_search_dependency_error_reaches_ui_reader(monkeypatch, reader):
    from media_cli.app import search_coordinator as search
    from media_cli.core.api import ProviderDependencyError

    class ImmediateThread:
        def __init__(self, target, **kwargs):
            self.target = target

        def start(self):
            self.target()

        def join(self):
            pass

    failure = ProviderDependencyError("Parser unavailable")
    monkeypatch.setattr(search, "_provider_search_cache", {})
    monkeypatch.setattr(search, "load_config", lambda: {})
    monkeypatch.setattr(search, "require_provider_operation", lambda *args: None)
    monkeypatch.setattr(search, "search_anime", Mock(side_effect=failure))
    monkeypatch.setattr(search.threading, "Thread", ImmediateThread)
    live, results, loading, error = search.make_provider_oneshot_search("example", "sub", "anipm")
    with pytest.raises(ProviderDependencyError) as caught:
        {"error": error, "results": results, "live": live}[reader]()
    assert caught.value is failure


def test_real_cli_missing_parser_reports_install_hint_and_nonzero_exit(tmp_path):
    script = """
import importlib.abc, socket, sys
class Guard(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'bs4' or fullname.startswith('bs4.'):
            raise ModuleNotFoundError('Unavailable for test', name=fullname)
sys.meta_path.insert(0, Guard())
def no_network(*args, **kwargs):
    raise AssertionError('No network before missing-library diagnostic')
socket.socket.connect = no_network
from media_cli import app_core
sys.argv = ['anyplay', 'search', 'animepahe:example', '--json', '--incognito']
from media_cli.cli.main import run
raise SystemExit(run())
"""
    result = subprocess.run([sys.executable, "-c", script], cwd=ROOT,
                            env={**os.environ, "HOME": str(tmp_path), "ALLMANGA_NO_OS_EXIT": "1"},
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "AnimePahe search requires beautifulsoup4" in result.stderr
    assert "pipx inject anyplay beautifulsoup4" in result.stderr
    assert "Traceback" not in result.stdout + result.stderr
    assert "unexpected error" not in (result.stdout + result.stderr).lower()


def test_cached_search_does_not_load_a_transport_it_will_not_use(monkeypatch):
    from media_cli.app import search_coordinator as search

    check = Mock(side_effect=AssertionError("Cached search must not load HTTP transport"))
    monkeypatch.setattr(search, "require_provider_operation", check)
    monkeypatch.setattr(search, "load_config", lambda: {})
    monkeypatch.setattr(search, "_provider_search_cache", {("example", "sub", "anipm"): []})
    live, results, loading, error = search.make_provider_oneshot_search("example", "sub", "anipm")
    assert results() == [] and error() == ""
    check.assert_not_called()


@pytest.mark.parametrize("provider", ["anipm", "reanime"])
@pytest.mark.parametrize("operation", ["search", "title"])
def test_catalog_browsing_does_not_require_playback_libraries(monkeypatch, provider, operation):
    module = requirements()
    importer = Mock(side_effect=AssertionError("Unused library imported"))
    monkeypatch.setattr(module.importlib, "import_module", importer)
    module.require_provider_operation(provider, operation)
    importer.assert_not_called()


def test_senshi_direct_sources_do_not_require_remote_transport_or_hls_parser(monkeypatch):
    from media_cli.providers.senshi import Senshi
    from media_cli.services import catalog

    module = requirements()
    monkeypatch.setattr(module.importlib, "import_module", Mock(side_effect=AssertionError("Unused import")))
    provider = Senshi()
    monkeypatch.setattr(provider, "_fetch_json", lambda *args: [{"url": "https://cdn1.anidap.biz/token/master.m3u8", "status": "HardSub"}])
    monkeypatch.setattr(catalog, "get_provider", lambda *args: provider)
    sources = catalog.get_episode_data("123", "1", provider_id="senshi")
    assert len(sources["episode"]["sourceUrls"]) == 1


def test_anipm_adaptive_fallback_survives_missing_quality_parser(monkeypatch):
    from media_cli.providers.shared.anipm_mirrors import collect_mirrors

    module = requirements()
    monkeypatch.setitem(sys.modules, "m3u8", None)
    real_import = module.importlib.import_module
    monkeypatch.setattr(module.importlib, "import_module", lambda name: (_ for _ in ()).throw(
        ModuleNotFoundError(name, name=name)) if name == "m3u8" else real_import(name))
    module.require_provider_operation("anipm", "sources")
    base = {"episode": {"sourceUrls": [{"streamUrl": "https://cdn.example/master.m3u8", "sourceName": "AniPM"}]}}
    result = collect_mirrors(base, "sub", {}, resolve_channel=lambda *args: {},
                             fetch_playlist=lambda *args, **kwargs: "#EXTM3U\n")
    assert result["episode"]["sourceUrls"][0]["streamUrl"] == "https://cdn.example/master.m3u8"


@pytest.mark.parametrize("has_direct", [False, True])
def test_senshi_missing_remote_dependency_preserves_other_available_sources(monkeypatch, has_direct):
    from media_cli.core.api import ProviderDependencyError
    from media_cli.providers.senshi import Senshi

    module = requirements()
    monkeypatch.setattr(module.importlib, "import_module", Mock(side_effect=ImportError("unavailable")))
    provider = Senshi()
    embeds = [{"remote_source_id": "111", "status": "HardSub"}]
    if has_direct:
        embeds.append({"url": "https://cdn1.anidap.biz/token/master.m3u8", "status": "HardSub"})
    monkeypatch.setattr(provider, "_fetch_json", lambda *args: embeds)
    remote = Mock(side_effect=AssertionError("Missing library checked before remote lookup"))
    monkeypatch.setattr(provider, "_resolve_remote_sources", remote)
    if has_direct:
        result = provider.episode_sources("123", "1", "sub")
        assert len(result["episode"]["sourceUrls"]) == 1
    else:
        with pytest.raises(ProviderDependencyError, match="curl-cffi"):
            provider.episode_sources("123", "1", "sub")
    remote.assert_not_called()


@pytest.mark.parametrize("provider,operation,package", [
    ("senshi", "remote_sources", "curl-cffi"),
])
def test_required_stream_operations_report_missing_backend(monkeypatch, provider, operation, package):
    from media_cli.core.api import ProviderDependencyError

    module = requirements()
    monkeypatch.setattr(module.importlib, "import_module", Mock(side_effect=ImportError("unavailable")))
    with pytest.raises(ProviderDependencyError, match=package):
        module.require_provider_operation(provider, operation)
