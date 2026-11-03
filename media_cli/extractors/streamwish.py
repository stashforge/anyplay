"""StreamWish and VidHide native stream extractor."""

from __future__ import annotations

import json
import re
import urllib.parse

from bs4 import BeautifulSoup

from .base import BaseExtractor
from .routing import ROUTES
from .unpack import JsUnpacker
from ..media.urls import validate_stream_url


class StreamWishExtractor(BaseExtractor):
    """Extractor for StreamWish, VidHide, Fastream, and affiliated video hosts."""

    name = "StreamWish"
    domains = ROUTES['streamwish'].domains
    patterns = ROUTES['streamwish'].patterns

    def _extract_app(self, url, origin, page_referer, request_headers, name, priority, subtitles):
        from ..media.player_metadata import decode_player_metadata

        parsed = urllib.parse.urlsplit(url)
        video_id = parsed.fragment.split('&', 1)[0].strip('/')
        if not video_id:
            return []
        query = urllib.parse.urlencode({
            'id': video_id, 'w': 1920, 'h': 1080,
            'r': (urllib.parse.urlsplit(page_referer).hostname or '').removeprefix('www.'),
        })
        payload = self.fetch_page(origin + '/api/v1/video?' + query,
                                  referer=url, headers=request_headers)
        data = decode_player_metadata(payload)
        if not isinstance(data.get('player'), dict):
            return []
        config = data.get('streamingConfig') or {}
        if isinstance(config, str):
            config = json.loads(config)
        if not isinstance(config, dict):
            return []
        tracks = [dict(track) for track in (subtitles or [])]
        api_tracks = data.get('subtitle') or []
        if isinstance(api_tracks, str):
            api_tracks = json.loads(api_tracks)
        if isinstance(api_tracks, dict):
            api_tracks = [{'label': label, 'url': location} for label, location in api_tracks.items()]
        if isinstance(api_tracks, list) and data['player'].get('allowExternal'):
            extra = urllib.parse.parse_qs(parsed.fragment.partition('&')[2]).get('subs', [])
            if extra:
                try:
                    embedded = json.loads(extra[0])
                    if isinstance(embedded, list):
                        api_tracks = [*api_tracks, *embedded]
                except (ValueError, TypeError):
                    pass
        if isinstance(api_tracks, list):
            for track in api_tracks:
                if not isinstance(track, dict):
                    continue
                location = track.get('url') or track.get('file')
                if isinstance(location, str) and location:
                    parts = urllib.parse.urlsplit(location)
                    language = track.get('language') or parts.fragment or parts.path.rsplit('/', 1)[-1][:2]
                    tracks.append({
                        'url': urllib.parse.urljoin(origin + '/', location),
                        'label': track.get('label') or 'Subtitles',
                        'language': str(language).lower(),
                        'default': bool(track.get('default')),
                    })
        if tracks and not any(track.get('default') for track in tracks):
            preferred = str(data['player'].get('defaultSubtitle') or 'en').lower()
            chosen = next((track for track in tracks if track.get('language') == preferred), tracks[0])
            chosen['default'] = True
        fields = {'Tiktok': 'hlsVideoTiktok', 'Google': 'hlsVideoGoogle', 'In-House': 'source'}
        order = config.get('order') or ['Tiktok', 'Google', 'Cloudflare', 'In-House']
        adjustments = config.get('adjust') or {}
        if not isinstance(order, list) or not isinstance(adjustments, dict):
            return []
        for delivery in order:
            # Cloudflare delivery needs rotating per-segment player tokens; do
            # not publish those links as directly playable before that is supported.
            field = fields.get(delivery)
            adjustment = adjustments.get(delivery) or {}
            if not field or not isinstance(adjustment, dict) or adjustment.get('disabled'):
                continue
            value = data.get(field)
            if not isinstance(value, str) or not value:
                continue
            try:
                master = urllib.parse.urlsplit(validate_stream_url(urllib.parse.urljoin(origin + '/', value)))
                path = master.path
                domain = adjustment.get('domain')
                if isinstance(domain, str) and domain:
                    path = path.replace('/hls/', '/hlsmod/' + urllib.parse.quote(domain, safe='.-') + '/', 1)
                params = dict(urllib.parse.parse_qsl(master.query, keep_blank_values=True))
                if isinstance(adjustment.get('params'), dict):
                    params.update(adjustment['params'])
                master_url = urllib.parse.urlunsplit(master._replace(path=path, query=urllib.parse.urlencode(params)))
                playlist = self.fetch_page(master_url, referer=origin + '/', headers=request_headers, timeout=8)
                if not playlist.lstrip().startswith('#EXTM3U'):
                    continue
                rows = self.extract_m3u8(
                    master_url, referer=origin + '/', origin=origin, name=name or self.name,
                    priority=priority, headers=request_headers, subtitles=tracks,
                    manifest_content=playlist,
                )
                for row in rows:
                    row['delivery_type'] = delivery
                return rows
            except Exception:
                continue
        return []

    def extract(
        self,
        url: str,
        *,
        name: str = "",
        priority: int = 2,
        subtitles: list[dict] | None = None,
        headers: dict[str, str] | None = None,
        referer: str | None = None,
    ) -> list[dict]:
        parsed = urllib.parse.urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        page_referer = referer or url
        referer = f"{origin}/"
        request_headers = {k: v for k, v in (headers or {}).items() if k.lower() not in ('referer', 'origin')}
        request_headers['Origin'] = origin

        html = self.fetch_page(url, referer=page_referer, headers=request_headers)
        if not html:
            return []

        soup = BeautifulSoup(html, "html.parser")
        master_url = ""
        extracted_subs = list(subtitles or [])

        for script_tag in soup.find_all("script"):
            data = script_tag.string or script_tag.text or ""
            if not data:
                continue

            if "eval(function(p,a,c" in data or "eval(function(p, a, c" in data:
                unpacked = JsUnpacker.unpack_and_combine(data)
            else:
                unpacked = data

            if "m3u8" in unpacked or "source" in unpacked or "file" in unpacked:
                # 1. file: "https://...m3u8"
                m = re.search(r'file\s*:\s*["\']([^"\']+\.m3u8[^"\']*)["\']', unpacked)
                if m:
                    master_url = m.group(1)

                # 2. sources: [{file: "..."}]
                if not master_url:
                    m = re.search(r'source[s]?\s*:\s*\[\s*\{[^}]*?file\s*:\s*["\']([^"\']+)["\']', unpacked)
                    if m:
                        master_url = m.group(1)

                # Extract embedded subtitles if present
                sub_match = re.search(r'tracks\s*:\s*(\[[^\]]+\])', unpacked)
                if sub_match:
                    try:
                        raw_tracks = json.loads(sub_match.group(1))
                        for t in raw_tracks:
                            if isinstance(t, dict) and t.get("kind") in ("captions", "subtitles"):
                                sub_file = t.get("file")
                                if sub_file:
                                    extracted_subs.append({
                                        "label": t.get("label", "Subtitles"),
                                        "url": urllib.parse.urljoin(url, sub_file),
                                        "default": bool(t.get("default")),
                                    })
                    except Exception:
                        pass

            if master_url:
                break

        if not master_url:
            if parsed.fragment and soup.find('script', type='module', src=True):
                return self._extract_app(url, origin, page_referer, request_headers,
                                         name, priority, extracted_subs)
            return []

        if master_url.startswith("//"):
            master_url = f"https:{master_url}"

        stream_name = name or self.name
        return self.extract_m3u8(
            master_url,
            referer=referer,
            origin=origin,
            name=stream_name,
            priority=priority,
            subtitles=extracted_subs,
            headers=request_headers,
        )
