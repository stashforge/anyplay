"""Stream extraction is a playback dependency, not a CLI startup dependency."""

import subprocess
import sys

from media_cli.core import reporting, streams


def test_cli_startup_does_not_import_stream_extraction():
    result = subprocess.run(
        [sys.executable, "-c", "\n".join((
            "import sys",
            "import media_cli.cli.main",
            "assert 'media_cli.media.resolver' not in sys.modules",
            "assert 'media_cli.extractors' not in sys.modules",
        ))],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr


def test_lazy_resolution_preserves_arguments_results_and_reporters(monkeypatch):
    from media_cli import app_core
    from media_cli.media import resolver

    source = {"sourceName": "test"}
    expected = [{"link": "https://example.org/video.mp4"}]
    calls = []
    messages = []
    monkeypatch.setattr(reporting, "info", messages.append)
    monkeypatch.setattr(resolver, "_info", lambda message: None)
    monkeypatch.setattr(resolver, "_ok", lambda message: None)
    monkeypatch.setattr(resolver, "_warn", lambda message: None)

    def resolve(value, silent=False):
        calls.append((value, silent))
        resolver._info("Resolving stream")
        return expected

    monkeypatch.setattr(resolver, "resolve_source", resolve)
    assert app_core.resolve_source is streams.resolve_source
    assert streams.resolve_source(source, silent=True) is expected
    assert calls == [(source, True)]
    assert calls[0][0] is source
    assert messages == ["Resolving stream"]
    assert resolver._ok is reporting.ok
    assert resolver._warn is reporting.warn
