"""Odysee's signed embed parameters must survive the yt-dlp handoff."""

import base64
import json
import subprocess
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from media_cli.media import ytdlp
from media_cli.providers.shared.wordpress import normalize_embed_url, parse_mirrors


@pytest.mark.parametrize('path', ['/$/embed/@channel:1/video:4', '/%24/embed/%40channel%3A1/video%3A4'])
def test_normalization_preserves_signed_query_and_fragment(path):
    query = 'r=share&signature=a%2Bb%2Fc%3D&signature_ts=123&extra=x+y&extra=z'
    normalized = normalize_embed_url('https://odysee.com' + path + '?' + query + '#player')
    parts = urlsplit(normalized)
    assert parts.path == '/@channel:1/video:4'
    assert parts.query == query
    assert parts.fragment == 'player'
    assert parse_qs(parts.query)['signature'] == ['a+b/c=']


def test_base64_mirror_retains_original_playback_parameters():
    embed = ('<iframe src="https://odysee.com/%24/embed/%40channel%3A1/video%3A4'
             '?r=share&amp;signature=signed%2Bvalue&amp;signature_ts=123"></iframe>')
    encoded = base64.b64encode(embed.encode()).decode()
    mirrors = parse_mirrors('https://animexin.dev', '<select class="mirror"><option value="' + encoded + '">Odysee</option></select>')
    assert len(mirrors) == 1
    assert mirrors[0].url == 'https://odysee.com/@channel:1/video:4?r=share&signature=signed%2Bvalue&signature_ts=123'


@pytest.mark.parametrize('impersonation_available', [True, False])
def test_valid_odysee_extraction_longer_than_six_seconds_succeeds(monkeypatch, impersonation_available):
    commands = []
    warnings = []
    payload = json.dumps({'formats': [{
        'url': 'https://cdn.example.com/video.mp4', 'width': 1920, 'height': 1080,
        'vcodec': 'h264', 'acodec': 'aac', 'protocol': 'https',
    }]}).encode()
    monkeypatch.setattr(ytdlp.shutil, 'which', lambda name: '/test/yt-dlp')

    def launch(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0 if impersonation_available or '--impersonate' not in command else 1)

    def read(process, timeout):
        if process.returncode:
            return b'Impersonate target chrome not available'
        # Model the observed valid extraction duration without waiting in the test.
        if timeout < 9.35:
            raise subprocess.TimeoutExpired('yt-dlp', timeout)
        return payload

    monkeypatch.setattr(ytdlp.subprocess, 'Popen', launch)
    monkeypatch.setattr(ytdlp, 'read_bounded_process_stdout', read)
    url = 'https://odysee.com/@channel:1/video:4?signature=value&signature_ts=123'
    streams = ytdlp.resolve_ytdlp_embed(url, name='Odysee', priority=1, ok=lambda msg: None, warn=warnings.append)
    assert len(streams) == 1
    assert streams[0]['resolution'].startswith('1080p')
    assert all(command[-1] == url for command in commands)
    assert not warnings


def test_unrelated_embed_keeps_existing_short_timeout(monkeypatch):
    warnings = []
    monkeypatch.setattr(ytdlp.shutil, 'which', lambda name: '/test/yt-dlp')
    monkeypatch.setattr(ytdlp.subprocess, 'Popen', lambda *args, **kwargs: SimpleNamespace(returncode=0))

    def read(process, timeout):
        if timeout < 9.35:
            raise subprocess.TimeoutExpired('yt-dlp', timeout)
        return b'{"formats": []}'

    monkeypatch.setattr(ytdlp, 'read_bounded_process_stdout', read)
    streams = ytdlp.resolve_ytdlp_embed('https://example.com/embed?site=odysee.com', name='Other', priority=1, ok=lambda msg: None, warn=warnings.append)
    assert streams == []
    assert any('timed out' in warning for warning in warnings)
