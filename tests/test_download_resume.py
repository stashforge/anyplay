from types import SimpleNamespace
from unittest.mock import Mock

from media_cli.media.download_queue_store import QueueStore
from media_cli.media.download_resume import bind_restored, resolve_restored
from media_cli.media.download_worker import DownloadItem, DownloadStatus, DownloadWorker


def test_restored_torrent_rebuilds_magnet_and_preserves_file_selection(tmp_path):
    item = DownloadItem('t', 'Batch', 'file.mkv', str(tmp_path/'file.mkv'), engine='libtorrent',
                        torrent={'infohash': 'a'*40, 'file_index': 3, 'file_name': 'file.mkv'})
    store = QueueStore(tmp_path/'queue.json')
    store.save([item])
    restored = store.load()[0]
    assert bind_restored(restored)
    url, headers, proxy = restored.resolve_fn(restored)
    assert 'urn%3Abtih%3A' + 'a'*40 in url
    assert restored.torrent['file_index'] == 3
    assert restored._resume_existing
    assert not restored.restored


def test_provider_restoration_fetches_fresh_links_and_tracks(tmp_path, monkeypatch):
    item = DownloadItem('a', 'Title', '1', str(tmp_path/'file.mkv'), ep_id='1',
                        show_metadata={'_provider': 'anipm', '_provider_id': '42'},
                        resume_request={'kind': 'provider', 'provider': 'anipm', 'show_id': '42',
                                        'episode_id': '1', 'translation': 'dub', 'quality': '1080p'})
    store = QueueStore(tmp_path/'queue.json')
    store.save([item])
    restored = store.load()[0]
    fetch = Mock(return_value=({'link': 'https://cdn.test/fresh.m3u8', 'subtitles': [{'lang': 'en'}],
                               'audio_url': 'https://cdn.test/audio', 'skip_intervals': [{'start': 2, 'end': 3}]}, 'native'))
    monkeypatch.setattr('media_cli.app_core.fetch_episode_stream', fetch)
    assert bind_restored(restored)
    assert resolve_restored(restored)[0] == 'https://cdn.test/fresh.m3u8'
    fetch.assert_called_once_with('42', '1', 'dub', '1080p', provider_id='anipm', exclude_sources=set())
    assert restored.audio_url == 'https://cdn.test/audio'
    assert restored.subtitles == [{'lang': 'en'}]


def test_direct_file_safe_link_roundtrip(tmp_path):
    item = DownloadItem('a', 'Title', '1', str(tmp_path/'file.mp4'), url='https://cdn.test/file.mp4')
    store = QueueStore(tmp_path/'queue.json')
    store.save([item])
    restored = store.load()[0]
    assert bind_restored(restored)
    assert resolve_restored(restored) == ('https://cdn.test/file.mp4', {}, None)


def test_missing_legacy_provider_does_not_guess_or_start(tmp_path):
    item = DownloadItem('old', 'Title', '1', str(tmp_path/'file'), restored=True)
    assert not bind_restored(item)
    assert 'provider identity' in item.error
    assert item.restored


def test_resume_restored_queues_without_network_in_ui_thread(tmp_path):
    item = DownloadItem('t', 'Title', 'file', str(tmp_path/'file'), status=DownloadStatus.PAUSED,
                        torrent={'infohash': 'a'*40, 'file_index': 0}, restored=True)
    worker = DownloadWorker.__new__(DownloadWorker)
    import threading
    worker.items = [item]
    worker.lock = threading.RLock()
    worker._wake_event = threading.Event()
    assert worker.resume_item('t')
    assert item.status == DownloadStatus.QUEUED
    assert callable(item.resolve_fn)
