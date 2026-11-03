"""Subtitle work must not hold video startup or outlive its playback owner."""

import threading
from types import SimpleNamespace

from media_cli.playback.subtitle_job import SubtitleJob
from media_cli.playback import desktop, mpv


def test_slow_preparation_is_nonblocking_and_retired_files_are_closed():
    entered, release, closed = (threading.Event() for _ in range(3))
    tracks = [{'url': 'https://example.test/sub.vtt', 'default': True}]

    def score(items, **kwargs):
        entered.set()
        assert release.wait(2)
        items[0]['label'] = 'prepared'
        return items

    def files(items, **kwargs):
        return SimpleNamespace(subtitles=items, close=closed.set)

    job = SubtitleJob(tracks, {}, '', 0, True, score=score, files=files)
    try:
        assert entered.wait(1)
        assert job.poll() is None
        job.close()
        release.set()
        job.thread.join(1)
        # Cancellation may skip files entirely; the worker must always retire.
        assert not job.thread.is_alive()
        assert job.poll() is None
        assert 'label' not in tracks[0]
    finally:
        release.set()
        job.close()


def test_ready_files_delivered_once_and_owned_until_playback_exits():
    closed = threading.Event()
    job = SubtitleJob([{'url': '/tmp/sub.vtt'}], {}, '', 0, True,
                      score=lambda items, **kw: items,
                      files=lambda items, **kw: SimpleNamespace(subtitles=items, close=closed.set))
    job.thread.join(1)
    assert job.poll() == [{'url': '/tmp/sub.vtt'}]
    assert job.poll() is None
    assert not closed.is_set()
    job.close()
    assert closed.is_set()


def test_resource_finishing_after_retirement_is_closed():
    entered, release, closed = (threading.Event() for _ in range(3))

    def files(items, **kwargs):
        entered.set()
        assert release.wait(2)
        return SimpleNamespace(subtitles=items, close=closed.set)

    job = SubtitleJob([{'url': 'sub'}], {}, '', 0, True,
                      score=lambda items, **kw: items, files=files)
    try:
        assert entered.wait(1)
        job.close()
        release.set()
        assert closed.wait(1)
        assert job.poll() is None
    finally:
        release.set()
        job.close()


def test_video_loads_with_audio_while_subtitles_are_still_preparing(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    received = []

    def score(items, **kwargs):
        entered.set()
        assert release.wait(2)
        return items

    def load(*args, **kwargs):
        assert entered.wait(1)
        assert not release.is_set()
        assert args[6] == 'https://example.test/audio.m4a'
        assert kwargs['subtitles'] == []

    def wait(*args, poll_subtitles, **kwargs):
        release.set()
        # The worker completes without touching mpv. Only the owner poll attaches.
        job = jobs[0]
        job.thread.join(1)
        assert received == []
        poll_subtitles()
        poll_subtitles()
        return 'QUIT', 0

    jobs = []

    def create(*args, **kwargs):
        job = SubtitleJob(*args, **kwargs, score=score)
        jobs.append(job)
        return job

    monkeypatch.setattr('media_cli.playback.subtitle_job.SubtitleJob', create)
    player = SimpleNamespace(props={}, _load_serial=3, load=load, wait_for_playback=wait,
                             queue_subtitles=lambda tracks, **kw: received.append((tracks, kw)))
    try:
        result = desktop.play_desktop(
            player, 'Title', '1', {'link': 'https://example.test/video.mp4',
                                  'audio_url': 'https://example.test/audio.m4a',
                                  'subtitles': [{'url': 'https://example.test/sub.vtt'}]},
            get_resume_time=lambda *a: 0, get_preferred_mirror=lambda *a: {},
            update_stream_info=lambda info: None, background_subtitles=True,
        )
        assert result[0] == 'QUIT'
        assert received == [([{'url': 'https://example.test/sub.vtt'}], {'load_serial': 3})]
    finally:
        release.set()


def test_late_subtitles_wait_for_file_and_cannot_attach_to_replacement():
    player = mpv.MpvIpc()
    commands = []
    player.send_cmd = lambda *args: commands.append(args)
    player._load_serial = 2
    player.file_loaded = False
    player.queue_subtitles([{'url': 'old'}], load_serial=1)
    assert not getattr(player, '_pending_subtitles', [])
    player.queue_subtitles([{'url': 'current'}], load_serial=2)
    assert commands == []
    player.file_loaded = True
    player.queue_subtitles([{'url': 'current'}], load_serial=2)
    assert commands == [('sub-add', 'current', 'select', 'Subtitle')]


def test_engine_enables_background_tracks_without_running_ui_adapters(monkeypatch):
    from media_cli.playback import engine
    calls = []
    monkeypatch.setattr(desktop, 'play_desktop', lambda *a, **kw: calls.append(kw) or 'result')
    assert engine.play_desktop('Title', '1', {}, cancel_check=lambda: False) == 'result'
    assert calls[0]['background_subtitles'] is True


def test_late_file_preparation_failure_preserves_remote_fallback():
    def files(*args, **kwargs):
        raise RuntimeError('unavailable')

    tracks = [{'url': 'https://example.test/sub.vtt'}]
    job = SubtitleJob(tracks, {}, '', 0, True, score=lambda items, **kw: items, files=files)
    try:
        job.thread.join(1)
        assert job.poll() == tracks
    finally:
        job.close()


def test_subtitles_ready_during_mpv_start_are_included_in_initial_load(monkeypatch):
    jobs = []
    tracks = [{'url': '/tmp/prepared.vtt'}]

    def create(*args, **kwargs):
        job = SubtitleJob(*args, **kwargs, score=lambda items, **kw: items)
        jobs.append(job)
        return job

    def load(*args, **kwargs):
        assert kwargs['subtitles'] == tracks
        assert args[7] == '/tmp/prepared.vtt'

    monkeypatch.setattr('media_cli.playback.subtitle_job.SubtitleJob', create)
    player = SimpleNamespace(props={}, load=load,
                             wait_for_playback=lambda *a, **kw: ('QUIT', 0))
    desktop.play_desktop(
        player, 'Title', '1', {'link': 'https://example.test/video.mp4', 'subtitles': tracks},
        get_resume_time=lambda *a: 0, get_preferred_mirror=lambda *a: {},
        update_stream_info=lambda info: None, background_subtitles=True,
        start_player_fn=lambda player: jobs[0].thread.join(1),
    )


def test_startup_cancellation_retires_subtitle_job_without_waiting(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    jobs = []

    def score(items, **kwargs):
        entered.set()
        assert release.wait(2)
        return items

    def create(*args, **kwargs):
        job = SubtitleJob(*args, **kwargs, score=score)
        jobs.append(job)
        return job

    def start(player):
        assert entered.wait(1)
        raise desktop.PlaybackPreparationCancelled

    monkeypatch.setattr('media_cli.playback.subtitle_job.SubtitleJob', create)
    try:
        result = desktop.play_desktop(
            SimpleNamespace(), 'Title', '1',
            {'link': 'https://example.test/video.mp4', 'subtitles': [{'url': 'sub'}]},
            get_resume_time=lambda *a: 0, get_preferred_mirror=lambda *a: {},
            update_stream_info=lambda info: None, background_subtitles=True, start_player_fn=start,
        )
        assert result[0] == 'CANCELLED'
        assert jobs[0]._retired.is_set()
        assert not release.is_set()
    finally:
        release.set()
        for job in jobs:
            job.thread.join(1)
