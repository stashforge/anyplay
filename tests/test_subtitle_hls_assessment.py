"""A partial HLS sample cannot establish the quality of an entire track."""
from contextlib import nullcontext
from types import SimpleNamespace
import logging

import pytest

from media_cli.media import subtitle_scorer


@pytest.mark.parametrize("encoding", ["plain", "bom", "gzip"])
@pytest.mark.parametrize("url", ["https://example.test/subs.m3u8", "https://example.test/subtitles?lang=en"])
def test_segmented_track_is_retained_without_scoring_empty_first_segment(monkeypatch, caplog, url, encoding):
    playlist = b"#EXTM3U\n#EXT-X-TARGETDURATION:10\n#EXTINF:10,\nempty.vtt\n#EXTINF:10,\ndialogue.vtt\n#EXT-X-ENDLIST\n"
    if encoding == "bom":
        playlist = b"\xef\xbb\xbf" + playlist
    elif encoding == "gzip":
        import gzip
        playlist = gzip.compress(playlist)
    requested = []
    def fetch(request, **kw):
        requested.append(request.full_url)
        data = (playlist if request.full_url == url else b"WEBVTT\n\n"
                if request.full_url.endswith("empty.vtt") else
                b"WEBVTT\n\n00:00:12.000 --> 00:00:14.000\nDialogue starts after silence.\n\n")
        return nullcontext(SimpleNamespace(read=lambda: data))
    monkeypatch.setattr("urllib.request.urlopen", fetch)
    monkeypatch.setattr(subtitle_scorer, "_SCORER_CACHE", {})
    caplog.set_level(logging.DEBUG, logger=subtitle_scorer.__name__)
    track = dict(url=url, label="English", default=True)
    result = subtitle_scorer.filter_subtitles([track], video_duration_s=1200)
    assert len(result) == 1 and result[0]["default"] is True
    assert result[0]["sub_label_status"] == "UNASSESSED"
    assert requested == [url], "Do not download segments solely to judge a partial sample"
    assert url not in subtitle_scorer._SCORER_CACHE
    assert "segmented_track_not_scored" in caplog.text
    assert "action=keep" in caplog.text


def test_complete_vtt_repetition_is_still_rejected(monkeypatch):
    content = b"WEBVTT\n\n" + b"\n\n".join(
        f"00:00:{2*i:02}.000 --> 00:00:{2*i+1:02}.000\nRepeated words".encode()
        for i in range(12)
    )
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **kw: nullcontext(SimpleNamespace(read=lambda: content)))
    monkeypatch.setattr(subtitle_scorer, "_SCORER_CACHE", {})
    assert subtitle_scorer.filter_subtitles([dict(url="https://example.test/full.vtt")], video_duration_s=1200) == []
