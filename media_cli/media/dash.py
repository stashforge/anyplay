"""DASH manifest and raw AllAnime stream construction."""

import math
import re
from xml.sax.saxutils import escape as xml_escape

from .urls import validate_stream_url


def _dash_range(value):
    text = str(value or "")
    return text if re.fullmatch(r"\d+-\d+", text) else "0-0"


def _dash_int(value):
    try:
        return max(0, int(value))
    except (TypeError, ValueError, OverflowError):
        return 0


def _xml_attribute(value):
    return xml_escape(str(value or ""), {'"': "&quot;", "'": "&apos;"})


def generate_dash_mpd(video, audio, duration):
    video_url = validate_stream_url(video.get("url", ""))
    audio_url = validate_stream_url(audio.get("url", ""))
    try:
        duration = max(0.001, float(duration))
    except (TypeError, ValueError):
        duration = 0.001
    if not math.isfinite(duration):
        duration = 0.001
    duration_iso = f"PT{duration:.3f}S"
    video_segment = video.get("segment_base") or {}
    audio_segment = audio.get("segment_base") or {}
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<MPD xmlns="urn:mpeg:dash:schema:mpd:2011"
     profiles="urn:mpeg:dash:profile:isoff-on-demand:2011"
     type="static" mediaPresentationDuration="{duration_iso}"
     minBufferTime="PT2S">
  <Period duration="{duration_iso}">
    <AdaptationSet mimeType="video/mp4" startWithSAP="1" segmentAlignment="true">
      <Representation id="video" bandwidth="{_dash_int(video.get("bandwidth"))}"
                      width="{_dash_int(video.get("width"))}"
                      height="{_dash_int(video.get("height"))}"
                      codecs="{_xml_attribute(video.get("codecs"))}">
        <BaseURL>{xml_escape(video_url)}</BaseURL>
        <SegmentBase indexRange="{_dash_range(video_segment.get("index_range"))}">
          <Initialization range="{_dash_range(video_segment.get("range"))}"/>
        </SegmentBase>
      </Representation>
    </AdaptationSet>
    <AdaptationSet mimeType="audio/mp4" startWithSAP="1" segmentAlignment="true">
      <Representation id="audio" bandwidth="{_dash_int(audio.get("bandwidth"))}"
                      codecs="{_xml_attribute(audio.get("codecs"))}">
        <BaseURL>{xml_escape(audio_url)}</BaseURL>
        <SegmentBase indexRange="{_dash_range(audio_segment.get("index_range"))}">
          <Initialization range="{_dash_range(audio_segment.get("range"))}"/>
        </SegmentBase>
      </Representation>
    </AdaptationSet>
  </Period>
</MPD>
'''


def resolve_dash_raw_urls(item, name, priority):
    raw_urls = item.get("rawUrls")
    if not isinstance(raw_urls, dict):
        return []
    videos = raw_urls.get("vids") or []
    audios = raw_urls.get("audios") or []
    avc_videos = [
        video
        for video in videos
        if "avc" in str(video.get("codecs") or "").casefold()
    ]
    candidates = avc_videos or videos
    valid_audio = [
        audio
        for audio in audios
        if str(audio.get("url") or "").startswith(("http://", "https://"))
    ]
    if not candidates or not valid_audio:
        return []

    best_audio = max(
        valid_audio,
        key=lambda audio: _dash_int(audio.get("bandwidth")),
    )
    best_by_height = {}
    for video in candidates:
        if not str(video.get("url") or "").startswith(("http://", "https://")):
            continue
        height = _dash_int(video.get("height"))
        current = best_by_height.get(height)
        if (
            current is None
            or _dash_int(video.get("bandwidth"))
            > _dash_int(current.get("bandwidth"))
        ):
            best_by_height[height] = video

    streams = []
    for height in sorted(best_by_height, reverse=True):
        video = best_by_height[height]
        try:
            video_url = validate_stream_url(video.get("url", ""))
            audio_url = validate_stream_url(best_audio.get("url", ""))
        except ValueError:
            continue
        resolution = f"{height}p" if height else "Adaptive"
        streams.append({
            "source_name": f"{name} DASH ({resolution})",
            "link": video_url,
            "type": "dash",
            "resolution": resolution,
            "referer": "",
            "headers": {},
            "source_priority": priority,
            "android_safe": False,
            "audio_url": audio_url,
            "dash_video": video,
            "dash_audio": best_audio,
            "dash_duration": raw_urls.get("duration"),
        })
    return streams
