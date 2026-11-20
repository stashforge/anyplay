"""Track retention and atomic finalization, independent of provider extraction."""
from pathlib import Path

import pytest

from media_cli.media import track_pipeline as pipeline


class Process:
    def __init__(self, lines=(), code=0):
        self.stdout = iter(lines)
        self.returncode = code
    def wait(self):
        return self.returncode
    def poll(self):
        return self.returncode


def fake_tools(monkeypatch, *, fail_merge=False, cancel_after_download=None):
    calls = []
    def start(command, **kwargs):
        calls.append(command)
        if command[0] == 'yt-dlp':
            output = Path(command[command.index('-o') + 1])
            output.write_bytes(b'media')
            if cancel_after_download is not None:
                cancel_after_download.append(True)
            return Process(['5\t5\t10\t0\ttrack\n'])
        if fail_merge:
            return Process(['Cannot write output\n'], 1)
        Path(command[-1]).write_bytes(b'final media')
        return Process()
    monkeypatch.setattr(pipeline.subprocess, 'Popen', start)
    monkeypatch.setattr(pipeline, 'probe_media', lambda path: ['video', 'audio', 'subtitle'])
    return calls


def test_merge_retry_reuses_completed_tracks_even_after_token_refresh(tmp_path, monkeypatch):
    calls = fake_tools(monkeypatch, fail_merge=True)
    target = tmp_path/'episode.mkv'
    with pytest.raises(pipeline.FinalizationError):
        pipeline.run_track_pipeline('https://cdn.test/video?token=old', target)
    assert not target.exists()
    assert len(list(tmp_path.rglob('track-0.mkv'))) == 1
    fake_tools(monkeypatch)
    reused = []
    pipeline.run_track_pipeline('https://cdn.test/video?token=new', target, message=reused.append)
    assert any('Reusing completed' in line for line in reused)
    assert target.read_bytes() == b'final media'
    assert not list(tmp_path.rglob('tracks.json'))
    assert len([cmd for cmd in calls if cmd[0] == 'yt-dlp']) == 1


def test_changed_media_invalidates_only_that_slot(tmp_path, monkeypatch):
    fake_tools(monkeypatch, fail_merge=True)
    target = tmp_path/'episode.mkv'
    audio = [{'url': 'https://cdn.test/audio', 'language': 'ja'}]
    with pytest.raises(pipeline.FinalizationError):
        pipeline.run_track_pipeline('https://cdn.test/one', target, audio_tracks=audio)
    calls = fake_tools(monkeypatch)
    pipeline.run_track_pipeline('https://cdn.test/two', target, audio_tracks=audio)
    downloads = [cmd for cmd in calls if cmd[0] == 'yt-dlp']
    assert len(downloads) == 1
    assert downloads[0][-1] == 'https://cdn.test/two'


def test_cancel_keeps_partial_and_does_not_publish(tmp_path, monkeypatch):
    cancelled = []
    calls = fake_tools(monkeypatch, cancel_after_download=cancelled)
    target = tmp_path/'episode.mkv'
    with pytest.raises(pipeline.PipelineCancelled):
        pipeline.run_track_pipeline('https://cdn.test/one', target, cancel=lambda: bool(cancelled))
    assert not target.exists()
    assert list(tmp_path.rglob('track-0.mkv'))
    assert all(cmd[0] != 'ffmpeg' for cmd in calls)


def test_local_merge_maps_languages_subtitles_and_metadata(tmp_path, monkeypatch):
    calls = fake_tools(monkeypatch)
    sub = tmp_path/'episode.en.vtt'
    sub.write_text('WEBVTT\n')
    pipeline.run_track_pipeline('https://cdn.test/video', tmp_path/'episode.mkv',
        audio_tracks=[{'url': 'https://cdn.test/audio', 'language': 'ja', 'label': 'Japanese'}],
        subtitle_files=[str(sub)], metadata_args=['-metadata', 'anyplay_episode=1'])
    command = calls[-1]
    assert command[0] == 'ffmpeg'
    inputs = [command[i+1] for i, arg in enumerate(command) if arg == '-i']
    assert len(inputs) == 3 and all(Path(path).is_file() or '.anyplay-tracks' in path for path in inputs)
    assert not any('https://' in path for path in inputs)
    assert 'language=jpn' in command and 'language=eng' in command
    assert 'anyplay_episode=1' in command
    assert '--continue' in calls[0] and '--part' in calls[0]


def test_corrupt_completed_track_is_downloaded_again(tmp_path, monkeypatch):
    fake_tools(monkeypatch, fail_merge=True)
    target = tmp_path/'episode.mkv'
    with pytest.raises(pipeline.FinalizationError):
        pipeline.run_track_pipeline('https://cdn.test/video', target)
    next(tmp_path.rglob('track-0.mkv')).write_bytes(b'corrupt size')
    calls = fake_tools(monkeypatch)
    pipeline.run_track_pipeline('https://cdn.test/video', target)
    assert len([cmd for cmd in calls if cmd[0] == 'yt-dlp']) == 1


def test_worker_merge_failure_does_not_retry_or_blacklist_mirror(tmp_path, monkeypatch):
    from media_cli.media.download_worker import DownloadWorker, DownloadItem, DownloadStatus
    worker = DownloadWorker(download_dir=str(tmp_path))
    item = DownloadItem('merge', 'Title', '1', str(tmp_path/'episode.mkv'),
                        url='https://cdn.test/video.mp4', engine='yt-dlp',
                        status=DownloadStatus.DOWNLOADING, current_mirror_name='Working mirror')
    monkeypatch.setattr('shutil.which', lambda tool: '/usr/bin/' + tool)
    def fail(item):
        raise pipeline.FinalizationError('Local merge failed; inputs retained')
    monkeypatch.setattr(worker, '_download_tracks', fail)
    try:
        worker._process_item(item)
        assert item.status == DownloadStatus.FAILED
        assert item.retry_count == 0
        assert item.current_mirror_name == 'Working mirror'
        assert 'inputs retained' in item.error
    finally:
        worker.stop()


def test_worker_tracks_finalize_before_completion_and_notify(tmp_path, monkeypatch):
    from media_cli.media import download_worker as module
    worker = module.DownloadWorker(download_dir=str(tmp_path))
    target = tmp_path/'episode.mkv'
    sub = tmp_path/'episode.en.vtt'
    sub.write_text('WEBVTT\n')
    notified = []
    item = module.DownloadItem('tracks', 'Title', '1', str(target),
            url='https://cdn.test/video', audio_tracks=[{'url': 'https://cdn.test/ja', 'language': 'ja'}],
            subtitles=[{'url': 'https://cdn.test/en.vtt', 'language': 'en'}],
            download_audio_langs='ja', download_sub_langs='en',
            status=module.DownloadStatus.DOWNLOADING, on_complete=lambda item: notified.append(item.status))
    monkeypatch.setattr('shutil.which', lambda tool: '/usr/bin/'+tool)
    monkeypatch.setattr(module, 'download_sidecar_subtitles', lambda *a, **kw: [str(sub)])
    monkeypatch.setattr(worker, '_episode_metadata_args', lambda item: (['-metadata', 'anyplay_episode=1'], True))
    def run(url, destination, **kwargs):
        assert item.status == module.DownloadStatus.DOWNLOADING
        assert kwargs['audio_tracks'][0]['language'] == 'ja'
        assert kwargs['subtitle_files'] == [str(sub)]
        assert 'anyplay_episode=1' in kwargs['metadata_args']
        target.write_bytes(b'final verified media')
        return str(target)
    monkeypatch.setattr(pipeline, 'run_track_pipeline', run)
    try:
        worker._download_tracks(item)
        assert item.status == module.DownloadStatus.COMPLETED
        assert notified == [module.DownloadStatus.COMPLETED]
        assert not sub.exists()
    finally:
        worker.stop()


def test_provider_signed_path_refresh_keeps_partial_and_fragment_state(tmp_path, monkeypatch):
    context = {'mirror': 'Sub 1080p', 'request': {'kind': 'provider', 'provider': 'anipm',
                'show_id': '42', 'episode_id': '1', 'translation': 'sub', 'quality': '1080p'}}
    cancelled = []
    fake_tools(monkeypatch, cancel_after_download=cancelled)
    target = tmp_path/'episode.mkv'
    with pytest.raises(pipeline.PipelineCancelled):
        pipeline.run_track_pipeline('https://cdn.test/signed-old', target, cache_context=context,
                                    cancel=lambda: bool(cancelled))
    slot = next(tmp_path.rglob('track-0.mkv')).parent
    (slot/'track-0.mkv').unlink()
    partial = slot/'track-0.mkv.part'
    partial.write_bytes(b'previous fragments')
    state = slot/'track-0.mkv.ytdl'
    state.write_text('{"downloader":{"current_fragment":{"index":12}}}')
    calls = fake_tools(monkeypatch)
    start = pipeline.subprocess.Popen
    def verify_preserved(command, **kwargs):
        if command[0] == 'yt-dlp':
            assert partial.read_bytes() == b'previous fragments'
            assert state.exists()
        return start(command, **kwargs)
    monkeypatch.setattr(pipeline.subprocess, 'Popen', verify_preserved)
    messages = []
    pipeline.run_track_pipeline('https://cdn.test/completely-new-signed-path', target,
                               cache_context=context, message=messages.append)
    assert any('18 bytes retained, fragment 12' in msg for msg in messages)
    assert len([cmd for cmd in calls if cmd[0] == 'yt-dlp']) == 1


def test_provider_resume_key_still_distinguishes_quality_translation_and_mirror():
    import copy
    context = {'mirror': 'Sub 1080p', 'request': {'kind': 'provider', 'provider': 'anipm',
                'show_id': '42', 'episode_id': '1', 'translation': 'sub', 'quality': '1080p'}}
    original = pipeline._track_key('https://cdn.test/old', context, {}, 0)
    assert original == pipeline._track_key('https://different.test/new', context, {}, 0)
    for key, value in [('episode_id', '2'), ('translation', 'dub'), ('quality', '720p')]:
        changed = copy.deepcopy(context)
        changed['request'][key] = value
        assert original != pipeline._track_key('https://cdn.test/old', changed, {}, 0)
    changed = {**context, 'mirror': 'Other encode'}
    assert original != pipeline._track_key('https://cdn.test/old', changed, {}, 0)


@pytest.mark.parametrize('exists,content,reason', [(False, b'', 'missing'), (True, b'', 'empty')])
def test_probe_reports_missing_and_empty_media(tmp_path, exists, content, reason):
    path = tmp_path / 'track.mkv'
    if exists:
        path.write_bytes(content)
    with pytest.raises(ValueError, match=reason):
        pipeline.probe_media(path)


def test_probe_retains_sanitized_ffprobe_reason(monkeypatch, tmp_path):
    from types import SimpleNamespace
    path = tmp_path / 'track.mkv'
    path.write_bytes(b'invalid')
    monkeypatch.setattr(pipeline.subprocess, 'run', lambda *a, **kw: SimpleNamespace(
        returncode=1, stdout='', stderr='Invalid data found https://cdn.test/file?token=private\n'))
    with pytest.raises(ValueError) as error:
        pipeline.probe_media(path)
    assert 'Invalid data found' in str(error.value)
    assert 'private' not in str(error.value)
    assert 'code 1' in str(error.value)


def test_verification_failure_reports_reason_and_downloader_tail(monkeypatch, tmp_path, caplog):
    fake_tools(monkeypatch)
    def fail(path):
        raise ValueError('Invalid data found when processing input')
    monkeypatch.setattr(pipeline, 'probe_media', fail)
    messages = []
    with pytest.raises(RuntimeError, match='Invalid data found'):
        pipeline.run_track_pipeline('https://cdn.test/video.mp4', tmp_path/'out.mkv', message=messages.append)
    assert any('Invalid data found' in message for message in messages)
    assert 'downloader tail:' in caplog.text
