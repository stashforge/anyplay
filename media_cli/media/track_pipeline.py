"""Resumable media acquisition followed by local-only, retryable finalization."""
from collections import deque
import hashlib
import json
import logging
import os
import re
from pathlib import Path
import shutil
import subprocess
from urllib.parse import urlsplit, urlunsplit

from ..state.io import atomic_write_json
from .languages import normalize_lang_code
from .urls import validate_stream_url


def safe_media_error(line: str) -> str:
    line = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", line)
    line = re.sub(r"https?://\S+", "[URL]", line, flags=re.I)
    line = re.sub(
        r"(authorization|cookie|set-cookie|token|password|secret|api[-_]?key)\s*[:=].*",
        r"\1=[redacted]", line, flags=re.I,
    )
    return " ".join("".join(char for char in line if char >= " " and char != "\x7f").split())[:300]


_logger = logging.getLogger(__name__)


class PipelineCancelled(Exception):
    pass


class FinalizationError(RuntimeError):
    """Local merge failed; retain tracks and do not rotate mirrors."""


def _source_key(url):
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, '', ''))


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _track_key(source, context, track, index):
    # Provider recipes name media independently of expiring CDN/proxy URLs.
    # Keep the selected mirror/quality in scope; never join different encodes.
    request = (context or {}).get('request', {})
    provider_scoped = request.get('kind') == 'provider' and all(
        request.get(key) not in (None, '') for key in ('provider', 'show_id', 'episode_id'))
    identity = ['provider-track', index] if provider_scoped else _source_key(source)
    return _digest([identity, context, track.get('language'), track.get('label')])


def probe_media(path):
    path = Path(path)
    if not path.is_file():
        raise ValueError('Downloaded media file is missing')
    if not path.stat().st_size:
        raise ValueError('Downloaded media file is empty')
    result = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'stream=codec_type',
                             '-of', 'json', str(path)], capture_output=True, text=True, timeout=20)
    if result.returncode:
        detail = safe_media_error(result.stderr or '') or 'no diagnostic output'
        raise ValueError(f'ffprobe exited with code {result.returncode}: {detail}')
    try:
        streams = json.loads(result.stdout).get('streams') or []
    except (ValueError, AttributeError) as exc:
        raise ValueError('ffprobe returned invalid media metadata') from exc
    types = [stream.get('codec_type') for stream in streams]
    if not any(kind in {'video', 'audio'} for kind in types):
        raise ValueError('Downloaded file contains no media tracks')
    return types


def run_track_pipeline(url, destination, *, headers=None, audio_tracks=(), subtitle_files=(),
                       metadata_args=(), identity=None, cache_context=None, message=lambda text: None,
                       progress=lambda *args: None, cancel=lambda: False,
                       process_changed=lambda proc: None, extra_args=()):
    """Subtitles are small local files; only video/audio are resumable transfers."""
    target = Path(destination).expanduser().absolute()
    stage = target.parent / '.anyplay-tracks' / _digest(str(target))[:24]
    stage.mkdir(parents=True, exist_ok=True, mode=0o700)
    manifest_path = stage / 'tracks.json'
    try:
        manifest = json.loads(manifest_path.read_text())
        if manifest.get('version') != 1 or not isinstance(manifest.get('tracks'), dict):
            raise ValueError('Unknown track manifest')
    except (OSError, ValueError, AttributeError):
        manifest = {'version': 1, 'tracks': {}}

    def check_cancel():
        if cancel():
            raise PipelineCancelled('Download interrupted; media tracks retained')

    def run(command, *, downloading=False, completed_bytes=0):
        check_cancel()
        diagnostics = deque(maxlen=8)
        proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, bufsize=1)
        process_changed(proc)
        try:
            for line in proc.stdout:
                check_cancel()
                values = line.strip().split('\t')
                if downloading and len(values) >= 5:
                    try:
                        done, total, speed = (max(0, int(float(value))) if value not in {'NA', 'None', ''} else 0
                                              for value in values[:3])
                        progress(completed_bytes + done, completed_bytes + total if total else 0,
                                 speed, values[3])
                    except (ValueError, OverflowError):
                        pass
                elif line.strip():
                    detail = safe_media_error(line.strip())
                    if detail:
                        diagnostics.append(detail)
                        if downloading and any(term in detail.lower() for term in ('resuming', 'unable to resume', 'restarting', 'already been downloaded')):
                            message(detail)
                            _logger.info('yt-dlp continuation: %s', detail)
            proc.wait()
            check_cancel()
            if proc.returncode:
                detail = ' | '.join(diagnostics) or f'exit code {proc.returncode}'
                raise RuntimeError(detail)
            return tuple(diagnostics)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            process_changed(None)

    sources = [(url, {}, identity or url)]
    sources += [(track['url'], track, track['url']) for track in audio_tracks
                if track.get('url') and track['url'] != url]
    paths, kinds = [], []
    completed_bytes = 0
    for index, (source, track, source_identity) in enumerate(sources):
        check_cancel()
        source = validate_stream_url(source)
        name = f'track-{index}.mkv'
        slot = stage / f'input-{index}'
        path = slot / name
        signature = _track_key(source_identity, cache_context, track, index)
        saved = manifest['tracks'].get(name, {})
        if saved.get('key') != signature:
            if saved.get('key'):
                notice = f'Track {index}: media identity changed; starting a separate transfer.'
                message(notice)
                _logger.info(notice)
            shutil.rmtree(slot, ignore_errors=True)
            saved = {'key': signature, 'complete': False}
        slot.mkdir(exist_ok=True, mode=0o700)
        reused = False
        if saved.get('complete') and path.is_file() and path.stat().st_size == saved.get('size'):
            try:
                types = probe_media(path)
                reused = True
            except (ValueError, OSError, subprocess.TimeoutExpired):
                reused = False
        if not reused:
            saved['complete'] = False
            manifest['tracks'][name] = saved
            atomic_write_json(str(manifest_path), manifest)
            partial_path = Path(str(path) + '.part')
            partial_bytes = partial_path.stat().st_size if partial_path.is_file() else 0
            state_path = Path(str(path) + '.ytdl')
            fragment = None
            try:
                fragment = json.loads(state_path.read_text()).get('downloader', {}).get('current_fragment', {}).get('index')
            except (OSError, ValueError, AttributeError):
                pass
            notice = (f'Resuming track {index}: {partial_bytes} bytes retained'
                      + (f', fragment {fragment}' if fragment is not None else '') + '.'
                      if partial_bytes else f'Starting track {index}: no reusable partial data.')
            message(notice)
            _logger.info('%s identity=%s', notice, signature[:12])
            command = ['yt-dlp', '--ignore-config', '--newline', '--continue', '--part',
                       '--merge-output-format', 'mkv', '--progress-template',
                       'download:%(progress.downloaded_bytes)s\t%(progress.total_bytes_estimate)s\t%(progress.speed)s\t%(progress.eta)s\ttrack',
                       '-o', str(path)]
            for key, value in (headers or {}).items():
                command.extend(['--add-header', f'{key}:{value}'])
            command.extend(extra_args)
            command.append(source)
            acquisition_notes = run(command, downloading=True, completed_bytes=completed_bytes)
            try:
                types = probe_media(path)
                if index and 'audio' not in types:
                    raise ValueError('Audio input contains no audio')
            except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
                reason = safe_media_error(str(exc)) or type(exc).__name__
                detail = f'Downloaded track verification failed: {name}: {reason}'
                message(detail)
                _logger.warning('%s; downloader tail: %s', detail, ' | '.join(acquisition_notes) or 'no diagnostic output')
                raise RuntimeError(detail) from exc
            saved.update(complete=True, size=path.stat().st_size)
            manifest['tracks'][name] = saved
            atomic_write_json(str(manifest_path), manifest)
        else:
            message(f'Reusing completed {"video" if index == 0 else "audio " + str(index)} track.')
        paths.append(path)
        kinds.append(types)
        completed_bytes += path.stat().st_size

    check_cancel()
    message('Finalizing locally: merging tracks, subtitles, metadata and skip timings…')
    progress(completed_bytes, completed_bytes, 0, '')
    # Preserve the final extension so FFmpeg selects the intended container.
    partial = stage / ('final' + (target.suffix or '.mkv'))
    command = ['ffmpeg', '-v', 'error', '-nostdin', '-y']
    for path in paths:
        command.extend(['-i', str(path)])
    for path in subtitle_files:
        command.extend(['-i', str(path)])
    command.extend(['-map', '0:v?'])
    if len(paths) > 1:
        for index, (_, track, _) in enumerate(sources[1:], 1):
            lang = normalize_lang_code(track.get('language') or track.get('label'), target='alpha3')
            command.extend(['-map', f'{index}:a:0', f'-metadata:s:a:{index-1}', f'language={lang}',
                            f'-metadata:s:a:{index-1}', 'title=' + str(track.get('label') or 'Audio')])
    else:
        command.extend(['-map', '0:a?'])
    # Keep existing embedded subtitles as well as separately supplied tracks.
    command.extend(['-map', '0:s?'])
    embedded_subs = kinds[0].count('subtitle')
    for index, path in enumerate(subtitle_files):
        language = Path(path).stem.rsplit('.', 1)[-1].split('-')[0]
        language = normalize_lang_code(language, target='alpha3')
        command.extend(['-map', f'{len(paths)+index}:s:0',
                        f'-metadata:s:s:{embedded_subs+index}', f'language={language}'])
    command.extend(metadata_args)
    command.extend(['-c', 'copy', str(partial)])
    try:
        run(command)
        types = probe_media(partial)
        if 'video' in kinds[0] and 'video' not in types:
            raise ValueError('Final video track missing')
        if (len(paths) > 1 or 'audio' in kinds[0]) and 'audio' not in types:
            raise ValueError('Final audio track missing')
        if subtitle_files and types.count('subtitle') < len(subtitle_files):
            raise ValueError('Final subtitle track missing')
        check_cancel()
        os.replace(partial, target)
    except PipelineCancelled:
        raise
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        raise FinalizationError(f'Finalization failed; downloaded tracks retained: {exc}') from exc
    finally:
        if partial.exists():
            partial.unlink()
    shutil.rmtree(stage, ignore_errors=True)
    try:
        stage.parent.rmdir()
    except OSError:
        pass
    message('Final file verified; temporary media tracks removed.')
    return str(target)
