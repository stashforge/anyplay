from pathlib import Path

import pytest

from media_cli.media import subtitles

URL = "https://media.test/subs/list?token=hidden"
PLAYLIST = b"#EXTM3U\n#EXT-X-TARGETDURATION:10\n#EXTINF:10,\na.vtt\n#EXTINF:10,\nb.vtt\n#EXT-X-ENDLIST\n"
SINGLE_FILE = b"#EXTM3U\n#EXT-X-TARGETDURATION:30\n#EXTINF:30,\nwhole.srt\n#EXT-X-ENDLIST\n"


def segment(cues, mapping=""):
    return ("WEBVTT\n" + mapping + "\n\n" + cues).encode()


def fetcher(monkeypatch, resources):
    calls = []

    def fetch(url, headers=None, referer=None):
        calls.append((url, headers, referer))
        return resources[url]

    monkeypatch.setattr(subtitles, "fetch_subtitle_bytes", fetch)
    return calls


def test_shared_assembler_keeps_global_times_and_deduplicates_overlap(monkeypatch):
    repeated = "cue-a\n00:00:09.000 --> 00:00:11.000 line:90%\n<i>Across\nthe boundary</i>\n\n"
    calls = fetcher(monkeypatch, {
        URL: PLAYLIST,
        "https://media.test/subs/a.vtt": segment(repeated),
        "https://media.test/subs/b.vtt": segment(repeated + "00:00:12.000 --> 00:00:13.000\nLater\n"),
    })
    data = subtitles.fetch_prepared_subtitle_bytes(URL, headers={"Origin": "https://media.test"}, referer="https://media.test/")
    assert data.count(b"WEBVTT") == 1
    assert data.count(b"Across") == 1
    assert b"00:00:12.000 --> 00:00:13.000" in data
    assert b"line:90%\n<i>Across\nthe boundary</i>" in data
    assert len(calls) == 3
    assert all(headers == {"Origin": "https://media.test"} and ref == "https://media.test/" for _, headers, ref in calls)


def test_single_complete_srt_in_hls_is_kept_as_a_local_subtitle(monkeypatch):
    from media_cli.media import mpv_subtitle_files

    data = b"1\n00:00:01,000 --> 00:00:03,000\n<i>One sentence.</i>\n\n2\n00:00:06,000 --> 00:00:07,000\nAnother.\n"
    calls = fetcher(monkeypatch, {URL: SINGLE_FILE, 'https://media.test/subs/whole.srt': data})
    tracks = [{'url': URL, 'label': 'English', 'default': True}]
    resource = mpv_subtitle_files.prepare_subtitle_files(tracks)
    try:
        path = Path(resource.subtitles[0]['url'])
        assert path.suffix == '.srt' and path.read_bytes() == data
        assert resource.subtitles[0]['default'] and resource.subtitles[0]['label'] == 'English'
        assert tracks[0]['url'] == URL
        assert len(calls) == 2
    finally:
        resource.close()
    assert not path.exists()


@pytest.mark.parametrize('data', [
    b'<html>not subtitles</html>', b'1\nnot a time --> not a time\nText\n',
    b'1\n00:00:02,000 --> 00:00:01,000\nText\n',
    b'1\n00:00:01,000 --> 00:00:02,000\n',
])
def test_single_file_wrapper_does_not_accept_invalid_srt(monkeypatch, data):
    fetcher(monkeypatch, {URL: SINGLE_FILE, 'https://media.test/subs/whole.srt': data})
    with pytest.raises(ValueError):
        subtitles.fetch_prepared_subtitle_bytes(URL)


def test_segment_local_timestamps_follow_mpegts_mapping(monkeypatch):
    fetcher(monkeypatch, {
        URL: PLAYLIST,
        "https://media.test/subs/a.vtt": segment("00:00:01.000 --> 00:00:02.000\nFirst\n", "X-TIMESTAMP-MAP=LOCAL:00:00:00.000,MPEGTS:900000"),
        "https://media.test/subs/b.vtt": segment("00:00:01.000 --> 00:00:02.000\nSecond\n", "X-TIMESTAMP-MAP=MPEGTS:1800000,LOCAL:00:00:00.000"),
    })
    data = subtitles.fetch_prepared_subtitle_bytes(URL)
    assert b"00:00:01.000 --> 00:00:02.000\nFirst" in data
    assert b"00:00:11.000 --> 00:00:12.000\nSecond" in data
    assert b"X-TIMESTAMP-MAP" not in data


@pytest.mark.parametrize("mutation", [
    lambda p: p.replace(b"#EXT-X-ENDLIST\n", b""),
    lambda p: p.replace(b"a.vtt", b"file:///etc/passwd"),
    lambda p: p.replace(b"#EXTINF:10,", b'#EXT-X-KEY:METHOD=AES-128,URI="key"\n#EXTINF:10,', 1),
    lambda p: p.replace(b"#EXTINF:10,", b"#EXT-X-BYTERANGE:100@0\n#EXTINF:10,", 1),
])
def test_unsupported_or_unsafe_playlist_never_fetches_segments(monkeypatch, mutation):
    calls = fetcher(monkeypatch, {URL: mutation(PLAYLIST)})
    with pytest.raises(ValueError):
        subtitles.fetch_prepared_subtitle_bytes(URL)
    assert len(calls) == 1


def test_cancel_between_segments_stops_requests(monkeypatch):
    from media_cli.media.subtitle_playlist import SubtitleAssemblyCancelled

    calls = fetcher(monkeypatch, {URL: PLAYLIST, "https://media.test/subs/a.vtt": segment("")})
    with pytest.raises(SubtitleAssemblyCancelled):
        subtitles.fetch_prepared_subtitle_bytes(URL, cancel_check=lambda: len(calls) >= 2)
    assert len(calls) == 2


def test_download_and_desktop_use_same_assembled_bytes(monkeypatch, tmp_path):
    from media_cli.media import mpv_subtitle_files

    fetcher(monkeypatch, {
        URL: PLAYLIST,
        "https://media.test/subs/a.vtt": segment("00:00:01.000 --> 00:00:02.000\nFirst\n"),
        "https://media.test/subs/b.vtt": segment("00:00:11.000 --> 00:00:12.000\nSecond\n"),
    })
    track = {"url": URL, "lang": "en", "label": "English", "default": True}
    files = subtitles.download_sidecar_subtitles([track], str(tmp_path / "episode"), quiet=True)
    assert len(files) == 1
    content = Path(files[0]).read_bytes()
    assert b"Second" in content and b"#EXTM3U" not in content
    resource = mpv_subtitle_files.prepare_subtitle_files([track])
    try:
        assert Path(resource.subtitles[0]["url"]).read_bytes() == content
    finally:
        resource.close()


def test_failed_segment_never_leaves_partial_download(monkeypatch, tmp_path):
    fetcher(monkeypatch, {URL: PLAYLIST, "https://media.test/subs/a.vtt": segment("")})
    assert subtitles.download_sidecar_subtitles([{"url": URL, "lang": "en"}], str(tmp_path / "ep"), quiet=True) == []
    assert list(tmp_path.iterdir()) == []


def test_clock_wrap_and_shared_styles_are_preserved(monkeypatch):
    before = (1 << 33) - 450000
    style = "STYLE\n::cue { color: lime; }\n\n"
    fetcher(monkeypatch, {
        URL: PLAYLIST,
        "https://media.test/subs/a.vtt": segment(style + "00:00:01.000 --> 00:00:02.000\nFirst\n", f"X-TIMESTAMP-MAP=LOCAL:00:00:00.000,MPEGTS:{before}"),
        "https://media.test/subs/b.vtt": segment(style + "00:00:01.000 --> 00:00:02.000\nSecond\n", "X-TIMESTAMP-MAP=LOCAL:00:00:00.000,MPEGTS:450000"),
    })
    data = subtitles.fetch_prepared_subtitle_bytes(URL)
    assert b"00:00:11.000 --> 00:00:12.000\nSecond" in data
    assert data.count(b"STYLE") == 1


def test_same_words_at_different_times_are_not_removed(monkeypatch):
    fetcher(monkeypatch, {
        URL: PLAYLIST,
        "https://media.test/subs/a.vtt": segment("00:00:01.000 --> 00:00:02.000\nAgain\n"),
        "https://media.test/subs/b.vtt": segment("00:00:11.000 --> 00:00:12.000\nAgain\n"),
    })
    assert subtitles.fetch_prepared_subtitle_bytes(URL).count(b"Again") == 2


@pytest.mark.parametrize("failure", ["invalid-vtt", "mixed-map", "size", "count", "deadline"])
def test_invalid_or_excessive_assembly_fails_without_output(monkeypatch, failure):
    from media_cli.media import subtitle_playlist

    first = segment("00:00:01.000 --> 00:00:02.000\nFirst\n")
    second = segment("")
    if failure == "invalid-vtt":
        second = b"<html>Not subtitles</html>"
    elif failure == "mixed-map":
        second = segment("", "X-TIMESTAMP-MAP=LOCAL:00:00:00.000,MPEGTS:900000")
    elif failure == "size":
        monkeypatch.setattr(subtitle_playlist, "MAX_SUBTITLE_BYTES", len(PLAYLIST))
    elif failure == "count":
        monkeypatch.setattr(subtitle_playlist, "MAX_SEGMENTS", 1)
    else:
        monkeypatch.setattr(subtitle_playlist, "ASSEMBLY_TIMEOUT", 0)
    fetcher(monkeypatch, {URL: PLAYLIST, "https://media.test/subs/a.vtt": first, "https://media.test/subs/b.vtt": second})
    with pytest.raises((ValueError, TimeoutError)):
        subtitles.fetch_prepared_subtitle_bytes(URL)


def test_download_cancel_cleans_previously_fetched_tracks(monkeypatch, tmp_path):
    from media_cli.media.subtitle_playlist import SubtitleAssemblyCancelled

    calls = fetcher(monkeypatch, {
        "https://media.test/first": segment("00:00:01.000 --> 00:00:02.000\nFirst\n"),
        URL: PLAYLIST,
    })
    with pytest.raises(SubtitleAssemblyCancelled):
        subtitles.download_sidecar_subtitles([
            {"url": "https://media.test/first", "lang": "en"}, {"url": URL, "lang": "es"},
        ], str(tmp_path / "episode"), quiet=True, cancel_check=lambda: len(calls) >= 2)
    assert len(calls) == 2
    assert list(tmp_path.iterdir()) == []


def test_download_cancel_during_assembly_never_launches_ffmpeg(monkeypatch, tmp_path):
    import threading

    from media_cli.media import download_worker
    from media_cli.media.subtitle_playlist import SubtitleAssemblyCancelled

    worker = object.__new__(download_worker.DownloadWorker)
    worker._stop_event = threading.Event()
    item = download_worker.DownloadItem(
        id="cancel", title="Fixture", episode="1", destination_path=str(tmp_path / "ep.mkv"),
        url="https://media.test/video", status=download_worker.DownloadStatus.DOWNLOADING,
        subtitles=[{"url": URL, "lang": "en"}], download_sub_langs="multi", download_audio_langs="multi",
    )

    def fetch(*a, **kw):
        item.status = download_worker.DownloadStatus.CANCELLED
        assert kw["cancel_check"]()
        raise SubtitleAssemblyCancelled

    monkeypatch.setattr(download_worker, "download_sidecar_subtitles", fetch)
    monkeypatch.setattr("media_cli.core.storage.load_config", lambda: {})
    monkeypatch.setattr(download_worker.subprocess, "Popen", lambda *a, **kw: pytest.fail("Cancelled download launched FFmpeg"))
    worker._download_ffmpeg(item)
    assert item.status == download_worker.DownloadStatus.CANCELLED


def test_download_combines_segmented_subtitles_into_one_mkv(monkeypatch, tmp_path):
    import json
    import shutil
    import subprocess

    from media_cli.media.download import download_episode

    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg and ffprobe required")
    video = tmp_path / "source.mkv"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=32x32:d=20:r=1",
                    "-c:v", "ffv1", str(video)], check=True, timeout=10, capture_output=True)
    fetcher(monkeypatch, {
        URL: PLAYLIST,
        "https://media.test/subs/a.vtt": segment("00:00:01.000 --> 00:00:02.000\nFirst\n"),
        "https://media.test/subs/b.vtt": segment("00:00:11.000 --> 00:00:12.000\nSecond\n"),
    })
    monkeypatch.setattr("media_cli.core.storage.load_config", lambda: {})
    target = tmp_path / "downloads"
    assert download_episode("Fixture", "1", {
        "link": str(video), "type": "mp4", "subtitles": [{"url": URL, "lang": "en"}],
    }, download_dir=str(target), downloader="ffmpeg", quiet=True, download_sub_langs="multi")
    outputs = [path for path in target.rglob("*") if path.is_file()]
    assert len(outputs) == 1 and outputs[0].suffix == ".mkv"
    result = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_packets", "-select_streams", "s",
                             "-of", "json", str(outputs[0])], capture_output=True, text=True, check=True, timeout=10)
    info = json.loads(result.stdout)
    assert len(info["streams"]) == 1 and info["streams"][0]["tags"]["language"] == "eng"
    assert [float(packet["pts_time"]) for packet in info["packets"]] == [1, 11]
