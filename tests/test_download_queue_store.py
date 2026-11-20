import json
from pathlib import Path

from media_cli.media.download_queue_store import QueueStore
from media_cli.media.download_worker import DownloadItem, DownloadStatus, DownloadWorker


def test_roundtrip_states_and_missing_completed_file(tmp_path):
    target = tmp_path / 'finished.mkv'
    target.write_bytes(b'data')
    items = [DownloadItem(str(i), 'Title', 'EP 1', str(target), status=status)
             for i, status in enumerate(DownloadStatus)]
    store = QueueStore(tmp_path / 'queue.json')
    store.save(items)
    restored = store.load()
    assert len(restored) == len(items)
    assert all(item.status == DownloadStatus.PAUSED for item in restored[:4])
    assert [item.status for item in restored[4:]] == [item.status for item in items[4:]]
    target.unlink()
    missing = store.load()[4]
    assert missing.error == 'Downloaded file is missing.'
    assert missing.status == DownloadStatus.COMPLETED


def test_never_saves_credentials_live_urls_callbacks_or_proxy(tmp_path):
    item = DownloadItem('id', 'Title', '1', str(tmp_path / 'out.mkv'),
                        url='http://localhost:123/token', headers={'Cookie': 'secret'},
                        resolve_fn=lambda item: None, proxy_server=object(),
                        show_metadata={'id': 'title-id', 'provider': 'anipm', 'headers': {'cookie': 'secret'}},
                        torrent={'infohash': 'a' * 40, 'file_index': 1})
    store = QueueStore(tmp_path / 'queue.json')
    store.save([item])
    saved = Path(store.path).read_text()
    assert 'secret' not in saved and 'localhost' not in saved
    assert 'resolve_fn' not in saved and 'proxy_server' not in saved
    restored = store.load()[0]
    assert restored.torrent['file_index'] == 1
    assert restored.show_metadata['provider'] == 'anipm'
    assert restored.resolve_fn is None and restored.restored
    assert Path(store.path).stat().st_mode & 0o077 == 0


def test_corrupt_and_invalid_rows_are_handled(tmp_path):
    store = QueueStore(tmp_path / 'queue.json')
    Path(store.path).write_text('invalid')
    assert store.load() == []
    Path(store.path).write_text(json.dumps({'version': 1, 'items': [None, {}, {'id': 3}]}))
    assert store.load() == []


def test_worker_restores_paused_without_starting_transfers_and_saves_new_rows(tmp_path):
    store = QueueStore(tmp_path / 'queue.json')
    store.save([DownloadItem('old', 'Title', '1', str(tmp_path / 'old.mkv'))])
    worker = DownloadWorker(str(tmp_path), queue_store=store)
    try:
        assert worker.items[0].status == DownloadStatus.PAUSED
        assert worker.active_items == {}
        assert not worker.resume_item('old')
        new = DownloadItem('old', 'Title', '1', str(tmp_path / 'old.mkv'), status=DownloadStatus.PAUSED)
        assert worker.add_item(new) is new
        assert len(worker.items) == 1
        assert store.load()[0].id == 'old'
    finally:
        worker.stop()


def test_legacy_missing_file_failure_recovers_only_published_completion(tmp_path):
    target = str(tmp_path / 'moved.mkv')
    finished = DownloadItem('published', 'Title', '1', target, status=DownloadStatus.FAILED,
                            progress=100, downloaded_bytes=20, total_bytes=20,
                            started_at=10, completed_at=20, output_files=[target])
    unverified = DownloadItem('unverified', 'Title', '2', target, status=DownloadStatus.FAILED,
                              progress=100, downloaded_bytes=20, total_bytes=20,
                              started_at=10, completed_at=20)
    store = QueueStore(tmp_path / 'queue.json')
    store.save([finished, unverified])
    legacy = json.loads(Path(store.path).read_text())
    for record in legacy["items"]:
        record.pop("completion_state_version", None)
    Path(store.path).write_text(json.dumps(legacy))
    recovered, failed = store.load()
    assert recovered.status == DownloadStatus.COMPLETED
    assert recovered.error == 'Downloaded file is missing.'
    assert failed.status == DownloadStatus.FAILED
