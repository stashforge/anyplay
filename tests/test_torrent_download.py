from pathlib import Path
from types import SimpleNamespace
import threading
import pytest
from media_cli.torrents.download import download_torrent_file
from media_cli.torrents.runtime import TorrentCancelled
from test_torrent_runtime import FakeInfo

HASH='a'*40


def fake_engine():
    events=[]
    state={'done':0,'stage':None}
    info=FakeInfo([('Batch/README.txt',2),('Batch/E01.mkv',4),('Batch/E02.mp4',8)])
    def priorities(values):
        events.append(('priorities',values))
    handle=SimpleNamespace(has_metadata=lambda:True,torrent_file=lambda:info,
        prioritize_files=priorities,unset_flags=lambda flag:events.append(('enable',flag)),
        pause=lambda:events.append(('pause',)), file_progress=lambda:[0,state['done'],0],
        status=lambda:SimpleNamespace(download_rate=4,progress=0,num_seeds=1,num_peers=1))
    def add(params):
        assert params.flags & 4
        assert not params.flags & 3
        state['stage']=Path(params.save_path)
        return handle
    def sleep(seconds):
        state['done']=4
        path=state['stage']/'Batch/E01.mkv'
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(b'data')
    lt=SimpleNamespace(torrent_flags=SimpleNamespace(upload_mode=4,auto_managed=2,paused=1),
        parse_magnet_uri=lambda magnet:SimpleNamespace(flags=3),
        session=lambda options:SimpleNamespace(add_torrent=add,remove_torrent=lambda handle:events.append(('remove',))))
    return lt,sleep,events,state


def test_selected_file_priorities_progress_and_original_container(tmp_path):
    lt,sleep,events,state=fake_engine()
    updates=[]
    target=tmp_path/'E01.mkv'
    path=download_torrent_file('magnet:?xt=urn:btih:'+HASH,{'infohash':HASH,'file_index':1,'file_name':'E01.mkv'},target,
        lt_module=lt,sleep=sleep,progress=lambda *values:updates.append(values))
    assert Path(path).read_bytes()==b'data'
    assert events[:2]==[('priorities',[0,4,0]),('enable',4)]
    assert events[-2:]==[('pause',),('remove',)]
    assert updates[-1]==(4,4,4)
    assert not state['stage'].exists()
    assert not (tmp_path/'E02.mp4').exists()


def test_cancel_retains_partials_and_never_publishes_destination(tmp_path):
    lt,sleep,events,state=fake_engine()
    def cancel():
        return state['stage'] is not None
    with pytest.raises(TorrentCancelled):
        download_torrent_file('magnet:test',{'infohash':HASH,'file_index':1},tmp_path/'out.mkv',
            lt_module=lt,sleep=sleep,cancel_check=cancel)
    assert not (tmp_path/'out.mkv').exists()
    assert events[-1]==('remove',)
    assert state['stage'].exists()


def test_worker_dispatches_magnet_to_libtorrent_not_ffmpeg(tmp_path,monkeypatch):
    from media_cli.media.download_worker import DownloadItem, DownloadStatus, DownloadWorker
    worker=DownloadWorker.__new__(DownloadWorker)
    worker.lock=threading.RLock()
    worker._stop_event=threading.Event()
    worker._wake_event=threading.Event()
    worker.active_items={}
    target=tmp_path/'out.mkv'
    completed=[]
    def download(url,metadata,destination,**kwargs):
        assert metadata['file_index']==1
        Path(destination).write_bytes(b'data')
        kwargs['progress'](4,4,4)
        return destination
    monkeypatch.setattr('media_cli.torrents.download.download_torrent_file',download)
    monkeypatch.setattr('media_cli.media.download_worker.plan_download',lambda *a,**kw:pytest.fail('HTTP engine used for magnet'))
    item=DownloadItem('test','Batch','E01.mkv',str(target),url='magnet:?xt=urn:btih:'+HASH,
                      torrent={'infohash':HASH,'file_index':1},on_complete=lambda item:completed.append(item.id))
    item.status=DownloadStatus.DOWNLOADING
    worker._process_item(item)
    assert item.status==DownloadStatus.COMPLETED
    assert item.progress==100 and item.output_files==[str(target)] and completed==['test']


def test_download_source_retains_selected_index_with_cold_provider_cache():
    from media_cli.torrents.sources import selected_file_source
    source=selected_file_source({'_id':HASH,'name':'Batch','_torrent':{'infohash':HASH,'name':'Batch','source':'nyaa','seeders':12}},'4','E03.mkv')
    assert source['torrent']['file_index']==3 and source['torrent']['file_name']=='E03.mkv'
    assert source['torrent']['seeders']==12


def test_plain_download_uses_same_torrent_backend(tmp_path,monkeypatch):
    from media_cli.media.download import download_episode
    monkeypatch.setattr('media_cli.core.storage.load_config',lambda:{})
    paths=[]
    def download(magnet,metadata,destination,**kwargs):
        assert metadata['file_index']==1
        paths.append(destination)
        return destination
    monkeypatch.setattr('media_cli.torrents.download.download_torrent_file',download)
    assert download_episode('Batch','E02.mp4',{'sourceUrl':'magnet:?xt=urn:btih:'+HASH,'torrent':{
        'infohash':HASH,'file_index':1,'file_name':'E02.mp4'}},download_dir=str(tmp_path),quiet=True)
    assert paths==[str(tmp_path/'Batch'/'E02.mp4')]
