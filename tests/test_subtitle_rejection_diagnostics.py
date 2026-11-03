"""Subtitle decisions explain their evidence without exposing URLs or dialogue."""
from contextlib import nullcontext
from types import SimpleNamespace
import logging

import pytest

from media_cli.media import subtitle_scorer


@pytest.fixture(autouse=True)
def scorer_cache(monkeypatch):
    monkeypatch.setattr(subtitle_scorer, "_SCORER_CACHE", {})


def test_rejection_logs_metrics_and_cached_decision_without_secrets(monkeypatch, caplog):
    content = "\n\n".join(f"{i}\n00:00:{2*i:02}.000 --> 00:00:{2*i+1:02}.000\nPRIVATE_DIALOGUE" for i in range(12)).encode()
    calls = []
    def fetch(*a, **kw):
        calls.append(True)
        return nullcontext(SimpleNamespace(read=lambda: content))
    monkeypatch.setattr("urllib.request.urlopen", fetch)
    caplog.set_level(logging.DEBUG, logger=subtitle_scorer.__name__)
    track = dict(url="https://subtitles.example.test/private-path.srt?token=SECRET_TOKEN", label="English")
    assert subtitle_scorer.filter_subtitles([track], video_duration_s=1200) == []
    assert subtitle_scorer.filter_subtitles([track], video_duration_s=1200) == []
    assert len(calls) == 1
    assert "action=reject" in caplog.text
    assert "verdict=JUNK" in caplog.text and "repetition=1.0" in caplog.text
    assert "cues=12" in caplog.text and "cached=True" in caplog.text
    assert "subtitles.example.test" in caplog.text
    for private in ["SECRET_TOKEN", "private-path", "PRIVATE_DIALOGUE"]:
        assert private not in caplog.text


def test_fetch_failure_keeps_track_and_explains_unscored_result(monkeypatch, caplog):
    def unavailable(*a, **kw):
        raise TimeoutError("secret request URL")
    monkeypatch.setattr("urllib.request.urlopen", unavailable)
    caplog.set_level(logging.DEBUG, logger=subtitle_scorer.__name__)
    result = subtitle_scorer.filter_subtitles([dict(url="https://example.test/sub.srt")])
    assert len(result) == 1
    assert "action=keep" in caplog.text and "fetch_or_parse_error" in caplog.text
    assert "TimeoutError" in caplog.text and "secret request URL" not in caplog.text


def test_diagnostics_cannot_break_existing_keep_on_error_for_malformed_url(monkeypatch, caplog):
    def unavailable(*a, **kw):
        raise ValueError("Malformed URL")
    monkeypatch.setattr("urllib.request.urlopen", unavailable)
    caplog.set_level(logging.DEBUG, logger=subtitle_scorer.__name__)
    result = subtitle_scorer.filter_subtitles([dict(url="https://[bad/sub.srt")])
    assert len(result) == 1
    assert "host=invalid" in caplog.text
