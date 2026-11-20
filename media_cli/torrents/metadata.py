"""Read a magnet's video inventory in upload-only mode, without payload requests."""
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
import time

from .runtime import import_libtorrent, _wait_for_metadata, VIDEO_EXTS


def video_inventory(info):
    files = info.files()
    result = []
    for index in range(files.num_files()):
        path = str(files.file_path(index))
        relative = PurePosixPath(path.replace('\\', '/'))
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('Torrent contains an unsafe file path')
        if relative.suffix.lower() in VIDEO_EXTS and int(files.file_size(index)) > 0:
            result.append({'index': index, 'path': path, 'size': int(files.file_size(index))})
    if not result:
        raise RuntimeError('No video files found in this torrent')
    return tuple(result)


def fetch_video_inventory(magnet, *, lt_module=None, timeout=60, cancel_check=None):
    lt = lt_module or import_libtorrent()
    # Fail closed on bindings too old to prevent payload requests during preview.
    upload_mode = getattr(lt.torrent_flags, 'upload_mode', None)
    if upload_mode is None:
        raise RuntimeError('Update libtorrent: metadata preview requires upload-only mode')
    session = lt.session({'enable_dht': True, 'enable_lsd': True,
                          'enable_upnp': False, 'enable_natpmp': False})
    handle = None
    with TemporaryDirectory(prefix='anyplay-torrent-metadata-') as directory:
        try:
            params = lt.parse_magnet_uri(magnet)
            params.save_path = str(Path(directory))
            params.flags |= upload_mode
            # Auto-managed queues must not clear upload mode behind our back.
            auto = getattr(lt.torrent_flags, 'auto_managed', 0)
            paused = getattr(lt.torrent_flags, 'paused', 0)
            params.flags &= ~(auto | paused)
            handle = session.add_torrent(params)
            _wait_for_metadata(handle, timeout=timeout, sleep=time.sleep,
                               cancel_check=cancel_check)
            return video_inventory(handle.torrent_file())
        finally:
            if handle is not None:
                handle.pause()
                session.remove_torrent(handle)
