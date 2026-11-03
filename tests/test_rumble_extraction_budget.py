"""Working Rumble mirrors must survive API and HLS metadata latency."""

import json
import subprocess
from types import SimpleNamespace

import pytest

from media_cli.media import ytdlp


@pytest.mark.parametrize('host', ['rumble.com', 'www.rumble.com'])
@pytest.mark.parametrize('impersonation_available', [True, False])
def test_working_rumble_metadata_survives_observed_latency(monkeypatch, host, impersonation_available):
    warnings = []
    payload = json.dumps({'formats': [{
        'url': 'https://cdn.example.com/video.m3u8', 'width': 1280, 'height': 536,
        'vcodec': 'h264', 'acodec': 'aac', 'protocol': 'm3u8_native',
    }]}).encode()
    monkeypatch.setattr(ytdlp.shutil, 'which', lambda name: '/test/yt-dlp')

    def launch(command, **kwargs):
        return SimpleNamespace(returncode=0 if impersonation_available or '--impersonate' not in command else 1)

    def read(process, timeout):
        if process.returncode:
            return b'Impersonate target chrome not available'
        # The live probe completed at 13.561s; model network time without sleeping.
        if timeout < 13.561:
            raise subprocess.TimeoutExpired('yt-dlp', timeout)
        return payload

    monkeypatch.setattr(ytdlp.subprocess, 'Popen', launch)
    monkeypatch.setattr(ytdlp, 'read_bounded_process_stdout', read)
    result = ytdlp.resolve_ytdlp_embed(
        f'https://{host}/embed/video/', name='Rumble', priority=1,
        ok=lambda message: None, warn=warnings.append,
    )
    assert result
    assert result[0]['resolution'].startswith('720p')
    assert not warnings


@pytest.mark.parametrize('host', ['rumble.com', 'rumble.com.example.org', 'example.org'])
def test_stalled_embed_still_has_a_bounded_deadline(monkeypatch, host):
    warnings = []
    monkeypatch.setattr(ytdlp.shutil, 'which', lambda name: '/test/yt-dlp')
    monkeypatch.setattr(ytdlp.subprocess, 'Popen', lambda *args, **kwargs: SimpleNamespace(returncode=0))

    def read(process, timeout):
        # No host may wait indefinitely; unrelated hosts retain the short deadline.
        limit = 20 if host == 'rumble.com' else 6
        assert timeout <= limit
        raise subprocess.TimeoutExpired('yt-dlp', timeout)

    monkeypatch.setattr(ytdlp, 'read_bounded_process_stdout', read)
    result = ytdlp.resolve_ytdlp_embed(
        f'https://{host}/embed/?site=rumble.com', name='Mirror', priority=1,
        ok=lambda message: None, warn=warnings.append,
    )
    assert result == []
    assert any('timed out' in message for message in warnings)
