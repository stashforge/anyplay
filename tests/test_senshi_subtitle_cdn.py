import pytest

from media_cli.providers.senshi import Senshi, _trusted_stream_url


def test_current_subtitle_cdn_reaches_both_languages_without_allowing_video():
    tracks = [
        {"url": "https://s.anicdn.se/sub/en.ass", "vtt_url": "https://s.anicdn.se/sub/en.vtt", "label": "English"},
        {"url": "https://s.anicdn.se/ai_dub/en.ass", "vtt_url": "https://s.anicdn.se/ai_dub/en.vtt", "label": "English (Dub)"},
        {"vtt_url": "https://img.anicdn.se/thumb.vtt", "label": "Thumbnails"},
    ]
    sub = Senshi._subtitles(tracks, False)
    dub = Senshi._subtitles(tracks, True)
    assert [s["url"] for s in sub] == [tracks[0]["url"]]
    assert [s["url"] for s in dub] == [tracks[1]["url"]]
    assert sub[0]["default"] is True and dub[0]["default"] is True
    assert not _trusted_stream_url(tracks[0]["url"])


@pytest.mark.parametrize("url", [
    "http://s.anicdn.se/en.ass", "https://s.anicdn.se.evil.test/en.ass",
    "https://evil-s.anicdn.se/en.ass", "https://s.anicdn.se@evil.test/en.ass",
    "https://user:pass@s.anicdn.se/en.ass", "https://s.anicdn.se:8443/en.ass",
    "https://img.anicdn.se/en.vtt", "https://127.0.0.1/en.ass",
    "https://s.anicdn.se/en.ass#other", "https://s.anicdn.se/\\evil",
])
def test_subtitle_cdn_rule_still_rejects_unapproved_or_unsafe_urls(url):
    assert Senshi._subtitles([{"url": url, "label": "English"}], False) == []
