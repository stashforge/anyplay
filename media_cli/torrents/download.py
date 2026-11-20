"""Selected-file torrent downloads, independent of terminal and HTTP engines."""
import logging
import os
from pathlib import Path
import re
import shutil
import time

from .metadata import video_inventory
from .runtime import import_libtorrent, _wait_for_metadata, TorrentCancelled

_log = logging.getLogger(__name__)


def download_torrent_file(magnet, metadata, destination, *, progress=None, message=None,
                          cancel_check=None, lt_module=None, sleep=time.sleep, upload_progress=None):
    infohash = str(metadata.get('infohash') or '').lower()
    index = metadata.get('file_index')
    if not re.fullmatch(r'[a-f0-9]{40}', infohash) or type(index) is not int or index < 0:
        raise ValueError('Torrent download requires a valid hash and selected file index; reopen its file list')
    lt = lt_module or import_libtorrent()
    target = Path(destination).expanduser().absolute()
    stage = target.parent / '.anyplay-torrent-partials' / infohash / str(index)
    stage.mkdir(parents=True, exist_ok=True)
    session = lt.session({'enable_dht': True, 'enable_lsd': True, 'enable_upnp': False, 'enable_natpmp': False})
    handle = None
    def cancelled():
        return bool(cancel_check and cancel_check())
    try:
        upload_mode = getattr(lt.torrent_flags, 'upload_mode', None)
        if upload_mode is None:
            raise RuntimeError('Update libtorrent: selected-file downloads require upload-only preparation')
        params = lt.parse_magnet_uri(magnet)
        params.save_path = str(stage)
        params.flags |= upload_mode
        params.flags &= ~(getattr(lt.torrent_flags, 'auto_managed', 0) | getattr(lt.torrent_flags, 'paused', 0))
        handle = session.add_torrent(params)
        if message:
            message('Fetching torrent metadata…')
        _wait_for_metadata(handle, timeout=60, sleep=sleep, cancel_check=cancelled)
        info = handle.torrent_file()
        selected = next((file for file in video_inventory(info) if file['index'] == index), None)
        if selected is None:
            raise ValueError('Selected video is not in this torrent')
        expected = metadata.get('file_name')
        if expected and Path(selected['path']).name != expected:
            raise ValueError('Selected torrent filename changed; reopen its file list')
        if metadata.get('file_path') and selected['path'] != metadata['file_path']:
            raise ValueError('Selected torrent path changed; reopen its file list')
        priorities = [0] * info.files().num_files()
        priorities[index] = 4
        handle.prioritize_files(priorities)
        handle.unset_flags(upload_mode)
        if message:
            message('Downloading selected torrent file; other files excluded.')
        _log.debug('Torrent download started: hash=%s file_index=%d bytes=%d', infohash, index, selected['size'])
        while True:
            if cancelled():
                raise TorrentCancelled('Torrent download interrupted; partial data retained')
            status = handle.status()
            error = getattr(status, 'errc', None)
            if error is not None and error.value():
                raise RuntimeError('libtorrent: ' + error.message())
            if upload_progress:
                upload_progress(max(0, int(getattr(status, "upload_rate", 0))), max(0, int(getattr(status, "total_upload", 0))))
            done = min(selected['size'], int(handle.file_progress()[index]))
            if progress:
                progress(done, selected['size'], max(0, int(status.download_rate)))
            if done >= selected['size']:
                break
            sleep(0.2)
        source = (stage / selected['path']).resolve()
        if not source.is_relative_to(stage.resolve()) or not source.is_file() or source.stat().st_size != selected['size']:
            raise RuntimeError('Torrent finished without the expected selected file')
    finally:
        if handle is not None:
            handle.pause()
            session.remove_torrent(handle)
    # Keep the original container, embedded audio and subtitles unchanged.
    target.parent.mkdir(parents=True, exist_ok=True)
    os.replace(source, target)
    shutil.rmtree(stage, ignore_errors=True)
    for parent in (stage.parent, stage.parent.parent):
        try:
            parent.rmdir()
        except OSError:
            pass
    _log.debug('Torrent download completed: hash=%s file_index=%d bytes=%d', infohash, index, selected['size'])
    return str(target)
