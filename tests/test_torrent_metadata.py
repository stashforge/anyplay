from types import SimpleNamespace
import pytest
from media_cli.torrents.metadata import video_inventory, fetch_video_inventory
from test_torrent_runtime import FakeInfo


def test_inventory_keeps_original_indexes_and_rejects_unsafe_paths():
    assert video_inventory(FakeInfo([('batch/readme.txt',10), ('batch/E01.mkv',100), ('batch/E02.mp4',200)])) == (
        {'index':1,'path':'batch/E01.mkv','size':100}, {'index':2,'path':'batch/E02.mp4','size':200})
    with pytest.raises(ValueError, match='unsafe'):
        video_inventory(FakeInfo([('../escape.mkv',100)]))


def test_preview_stays_upload_only_and_removes_handle():
    flags = SimpleNamespace(upload_mode=4, auto_managed=2, paused=1)
    params = SimpleNamespace(flags=3)
    events = []
    handle = SimpleNamespace(has_metadata=lambda:True,
        torrent_file=lambda:FakeInfo([('episode.mkv',100)]), pause=lambda:events.append('paused'),
        status=lambda:SimpleNamespace(num_peers=1,num_seeds=1,download_rate=0,progress=0))
    def add(value):
        assert value.flags & 4 and not value.flags & 3
        events.append('added')
        return handle
    session = SimpleNamespace(add_torrent=add,remove_torrent=lambda h:events.append('removed'))
    lt = SimpleNamespace(torrent_flags=flags,parse_magnet_uri=lambda magnet:params,session=lambda options:session)
    assert fetch_video_inventory('magnet:test',lt_module=lt)[0]['index'] == 0
    assert events == ['added','paused','removed']


def test_provider_file_catalog_cached_and_selected_index_reaches_source(monkeypatch):
    from media_cli.providers.torrent import TorrentProvider
    calls=[]
    def inventory(magnet):
        calls.append(magnet)
        return ({'index':1,'path':'Batch/E01.mkv','size':100}, {'index':3,'path':'Batch/E02.mkv','size':200})
    monkeypatch.setattr('media_cli.torrents.metadata.fetch_video_inventory',inventory)
    provider=TorrentProvider(search_fn=lambda q:[])
    cat=provider.episode_catalog('a'*40)
    assert cat['ids'] == ['2','4']
    provider.episode_catalog('a'*40,'dub')
    assert len(calls) == 1
    source=provider.episode_sources('a'*40,'4')['episode']['sourceUrls'][0]
    assert source['torrent']['file_index'] == 3
    assert source['torrent']['file_path'] == 'Batch/E02.mkv'
    with pytest.raises(ValueError,match='not in'):
        provider.episode_sources('a'*40,'1')
