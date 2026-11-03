"""Native public Odysee RPC resolution and advertised HLS renditions."""

from __future__ import annotations

import json
import re
import urllib.request
from urllib.parse import parse_qs, unquote, urlsplit, urlunsplit

from .base import BaseExtractor
from .routing import ROUTES
from ..media.sources import format_source_label, quality_from_dimensions
from ..media.urls import validate_stream_url
from ..services.http import SSL_CTX, UA

_MAX_METADATA = 2 * 1024 * 1024
_REFERER = 'https://odysee.com/'


def _quality(width, height):
    # Odysee advertises exact low-resolution heights even for widescreen video.
    if height in (144, 240, 360, 480, 720, 1080, 1440, 2160):
        return f'{height}p ({width}×{height})', height
    return quality_from_dimensions(width, height)


class OdyseeExtractor(BaseExtractor):
    name = 'Odysee'
    domains = ROUTES['odysee'].domains
    patterns = ROUTES['odysee'].patterns

    def _rpc(self, method, params):
        request = urllib.request.Request(
            'https://api.lbry.tv/api/v1/proxy',
            data=json.dumps({'method': method, 'params': params}).encode(),
            headers={'Content-Type': 'application/json-rpc', 'User-Agent': UA, 'Referer': _REFERER},
        )
        try:
            with urllib.request.urlopen(request, context=SSL_CTX, timeout=8) as response:
                data = response.read(_MAX_METADATA + 1)
            if len(data) > _MAX_METADATA:
                raise ValueError
            payload = json.loads(data)
            if payload.get('error') or not isinstance(payload.get('result'), dict):
                raise ValueError
            return payload['result']
        except Exception:
            # Resolver reports exception messages; never include signed URLs or API bodies.
            raise RuntimeError(f'Odysee {method} request unavailable') from None

    def _read_stream(self, url, *, method='GET', headers):
        request = urllib.request.Request(validate_stream_url(url), headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, context=SSL_CTX, timeout=8) as response:
                final = validate_stream_url(response.url)
                content_type = response.headers.get('Content-Type', '').split(';')[0].lower()
                data = response.read(1024) if method != 'HEAD' else b''
                if data.lstrip().startswith(b'#EXTM3U'):
                    data += response.read(_MAX_METADATA)
                    if len(data) > _MAX_METADATA:
                        raise ValueError
                return final, content_type, data.decode('utf-8-sig', errors='replace')
        except Exception:
            raise RuntimeError('Odysee stream request unavailable') from None

    def extract(self, url, *, name='', priority=2, subtitles=None, headers=None, referer=None, **kwargs):
        if not self.can_handle(url):
            return []
        parts = urlsplit(url)
        path = unquote(parts.path).strip('/')
        for prefix in ('$/embed/', '$/download/'):
            if path.startswith(prefix):
                path = path[len(prefix):]
                break
        if not path or path.startswith('$'):
            return []
        uri = 'lbry://' + (path.replace(':', '#') if path.startswith('@') else path.replace('/', ':').replace(':', '#'))
        resolved = self._rpc('resolve', {'urls': [uri]}).get(uri) or {}
        value = resolved.get('value') or {}
        if value.get('stream_type') != 'video':
            return []
        query = parse_qs(parts.query)
        params = {'uri': uri}
        params.update({key: query[key][0] for key in ('signature', 'signature_ts') if query.get(key)})
        stream_url = validate_stream_url(self._rpc('get', params)['streaming_url'])
        req_headers = {'User-Agent': (headers or {}).get('User-Agent') or UA, 'Referer': _REFERER}
        prefix = name or self.name
        streams = []

        # HEAD follows the streaming redirect to the advertised HLS master.
        # Do not GET the entire original file or infer unadvertised quality URLs.
        try:
            final, content_type, _ = self._read_stream(stream_url, method='HEAD', headers=req_headers)
            if '.m3u8' in urlsplit(final).path.lower() or 'mpegurl' in content_type:
                final, _, playlist = self._read_stream(final, headers=req_headers)
                if playlist.lstrip().startswith('#EXTM3U'):
                    streams = self.extract_m3u8(
                        final, referer=_REFERER, name=prefix, priority=priority,
                        headers=req_headers, subtitles=subtitles, manifest_content=playlist,
                        quality_labeler=_quality,
                    )
                    if any(s['resolution'] != 'Adaptive' for s in streams):
                        best = max(streams, key=lambda s: s.get('_quality_rank', 0))
                        streams.append({
                            'link': final, 'type': 'hls', 'resolution': 'Adaptive',
                            'source_name': format_source_label(prefix, 'Adaptive'),
                            'source_parent_name': prefix, 'source_priority': priority,
                            'headers': req_headers, 'referer': _REFERER, 'android_safe': True,
                            'subtitles': list(subtitles or []), '_max_quality_rank': best['_quality_rank'],
                            'max_resolution': best['resolution'],
                        })
        except Exception:
            pass
        try:
            # The public v3 GET endpoint serves the original file. Preserve all
            # signed query parameters while changing only the API version path.
            parsed = urlsplit(stream_url)
            original_url = urlunsplit(parsed._replace(path=re.sub(r'/api/v\d+/', '/api/v3/', parsed.path)))
            final, content_type, _ = self._read_stream(original_url, headers=req_headers)
            if content_type == 'video/mp4':
                video = value.get('video') or {}
                quality, rank = _quality(int(video.get('width') or 0), int(video.get('height') or 0))
                quality = f'{quality} Original' if video.get('width') or video.get('height') else 'Original'
                streams.append({
                    'link': final, 'type': 'mp4', 'resolution': quality,
                    'source_name': format_source_label(prefix, quality),
                    'source_parent_name': prefix, 'source_priority': priority,
                    'headers': req_headers, 'referer': _REFERER, 'android_safe': True,
                    'subtitles': list(subtitles or []), '_quality_rank': rank,
                    '_is_original': True,
                })
        except Exception:
            pass
        for stream in streams:
            if (value.get('video') or {}).get('duration'):
                stream['duration'] = value['video']['duration']
        return streams
