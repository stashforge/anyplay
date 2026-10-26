"""Native channel/quality enumeration never depends on yt-dlp."""

from copy import deepcopy
from urllib.parse import parse_qs, urlsplit

import pytest

from media_cli.providers.shared.anipm_mirrors import collect_mirrors

MASTER = '''#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=380000,RESOLUTION=640x360,CODECS="avc1.4d401e,mp4a.40.2"
360/index
#EXT-X-STREAM-INF:BANDWIDTH=900000,RESOLUTION=1280x720
720/index
#EXT-X-STREAM-INF:BANDWIDTH=1800000,RESOLUTION=1920x1080
1080/index
'''


def payload(channel):
    return {"episode": {"sourceUrls": [{"streamUrl": f"https://example.org/{channel}/master",
        "sourceName": "AniPM (Auto)", "resolution": "auto", "type": "hls",
        "expiresAt": 2000, "requires_proxy": True, "allow_ytdlp_expansion": False,
        "headers": {"Origin": "https://embed.settlar.io"}, "subtitles": [{"url": "https://example.org/sub"}],
        "skip_intervals": [{"type": "op", "start": 5, "end": 90, "source": "provider"}]}]}}


def test_sub_mode_only_resolves_sub_and_hard_sub_qualities():
    calls = []

    def resolve(channel):
        calls.append(channel)
        return payload(channel)

    original = payload("sub")
    before = deepcopy(original)
    result = collect_mirrors(original, "sub", {"sub": True, "subHard": True, "dub": True, "dubHard": False},
                             resolve_channel=resolve, fetch_playlist=lambda *a, **kw: MASTER)
    sources = result["episode"]["sourceUrls"]
    assert len(sources) == 6
    assert calls == ["subhard"]
    assert [row["resolution"].split()[0] for row in sources[:3]] == ["1080p", "720p", "360p"]
    assert {row["channel"] for row in sources} == {"sub", "subhard"}
    assert all("Auto" not in row["sourceName"] for row in sources)
    for row in sources:
        assert row["requires_proxy"] and row["allow_ytdlp_expansion"] is False
        assert row["expiresAt"] == 2000
        assert row["headers"] and row["subtitles"] and row["skip_intervals"]
    assert original == before


def test_external_audio_keeps_master_and_selects_resolution_through_proxy():
    master = MASTER.replace("#EXTM3U", '#EXTM3U\n#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="audio",NAME="Japanese",URI="audio/index"')
    master = master.replace("BANDWIDTH=", 'AUDIO="audio",BANDWIDTH=')
    result = collect_mirrors(payload("sub"), "sub", {}, resolve_channel=lambda channel: None,
                             fetch_playlist=lambda *a, **kw: master)
    source = result["episode"]["sourceUrls"][0]
    assert urlsplit(source["streamUrl"]).path == "/sub/master"
    assert parse_qs(urlsplit(source["streamUrl"]).query) == {"hls_res": ["1080"]}


@pytest.mark.parametrize("text", ["bad", "#EXTM3U\n#EXTINF:5,\nsegment.ts\n#EXT-X-ENDLIST"])
def test_non_master_playlist_retains_one_adaptive_source(text):
    result = collect_mirrors(payload("sub"), "sub", {}, resolve_channel=lambda channel: None,
                             fetch_playlist=lambda *a, **kw: text)
    assert len(result["episode"]["sourceUrls"]) == 1
    assert result["episode"]["sourceUrls"][0]["resolution"] == "auto"
    assert not result.get("dependency_hints")


def test_failed_channel_does_not_remove_good_channels():
    def resolve(channel):
        raise OSError("secret signed URL")

    result = collect_mirrors(payload("sub"), "sub", {"subHard": True}, resolve_channel=resolve,
                             fetch_playlist=lambda *a, **kw: MASTER)
    assert len(result["episode"]["sourceUrls"]) == 3


def test_duplicate_and_unsafe_variants_are_not_mirrors():
    master = MASTER + '#EXT-X-STREAM-INF:BANDWIDTH=1800000,RESOLUTION=1920x1080\n1080/index\n'
    master += '#EXT-X-STREAM-INF:BANDWIDTH=1800000,RESOLUTION=1920x1080\njavascript:bad\n'
    result = collect_mirrors(payload("sub"), "sub", {}, resolve_channel=lambda channel: None,
                             fetch_playlist=lambda *a, **kw: master)
    assert len(result["episode"]["sourceUrls"]) == 3


def test_dub_mode_excludes_sub_and_does_not_resolve_it():
    calls = []

    def resolve(channel):
        calls.append(channel)
        return payload(channel)

    result = collect_mirrors(payload("dub"), "dub", {"sub": True, "subHard": True, "dub": True, "dubHard": True},
                             resolve_channel=resolve, fetch_playlist=lambda *a, **kw: MASTER)
    assert [row["channel"] for row in result["episode"]["sourceUrls"]][::3] == ["dub", "dubhard"]
    assert calls == ["dubhard"]
