"""Assemble finite WebVTT HLS tracks without exposing HLS to subtitle renderers."""

import re
import time

from .urls import validate_http_url

MAX_SEGMENTS = 2048
MAX_SUBTITLE_BYTES = 32 * 1024 * 1024
ASSEMBLY_TIMEOUT = 90
_CLOCK_WRAP = 1 << 33
_TIMING = re.compile(r"(?P<start>(?:\d{2,}:)?\d{2}:\d{2}\.\d{3})\s+-->\s+(?P<end>(?:\d{2,}:)?\d{2}:\d{2}\.\d{3})(?P<settings>[^\n]*)$")


class SubtitleAssemblyCancelled(Exception):
    """A caller retired subtitle assembly between network requests."""


def _milliseconds(value):
    parts = value.split(":")
    if len(parts) not in (2, 3):
        raise ValueError("Invalid WebVTT timestamp")
    hours, minutes = (int(parts[0]), int(parts[1])) if len(parts) == 3 else (0, int(parts[0]))
    seconds, milliseconds = parts[-1].split(".")
    if minutes >= 60 or int(seconds) >= 60 or hours < 0:
        raise ValueError("Invalid WebVTT timestamp")
    return ((hours * 60 + minutes) * 60 + int(seconds)) * 1000 + int(milliseconds)


def _timestamp(value):
    if value < 0:
        raise ValueError("Negative assembled subtitle timestamp")
    seconds, milliseconds = divmod(value, 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02}:{minutes:02}:{seconds:02}.{milliseconds:03}"


def _parse_segment(data):
    text = data.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n").strip()
    blocks = re.split(r"\n(?:[ \t]*\n)+", text)
    header = blocks.pop(0).splitlines()
    if not header or not re.fullmatch(r"WEBVTT(?:[ \t].*)?", header[0]):
        raise ValueError("Expected WebVTT segment")
    mapping = None
    for line in header[1:]:
        if line.startswith("X-TIMESTAMP-MAP="):
            fields = dict(part.strip().split(":", 1) for part in line.split("=", 1)[1].split(","))
            pts = int(fields["MPEGTS"])
            if not 0 <= pts < _CLOCK_WRAP:
                raise ValueError("Invalid WebVTT clock")
            mapping = (pts, _milliseconds(fields["LOCAL"]))
    cues, styles = [], []
    for block in blocks:
        lines = block.splitlines()
        if not lines or lines[0] == "NOTE" or lines[0].startswith(("NOTE ", "NOTE\t")):
            continue
        if lines[0] in ("STYLE", "REGION"):
            styles.append(block)
            continue
        index = 0 if "-->" in lines[0] else 1
        timing = _TIMING.fullmatch(lines[index]) if len(lines) > index else None
        if timing is None or len(lines) <= index + 1:
            raise ValueError("Invalid WebVTT cue")
        start, end = _milliseconds(timing["start"]), _milliseconds(timing["end"])
        if end <= start:
            raise ValueError("Invalid WebVTT cue range")
        cues.append((start, end, timing["settings"], "\n".join(lines[index + 1:]), lines[0] if index else ""))
    return mapping, cues, styles


def assemble_subtitle_playlist(url, data, *, fetch, cancel_check=None):
    """Return a single VTT; incomplete/live/encrypted tracks fail without partial output."""
    deadline = time.monotonic() + ASSEMBLY_TIMEOUT

    def check():
        if cancel_check and cancel_check():
            raise SubtitleAssemblyCancelled
        if time.monotonic() >= deadline:
            raise TimeoutError("Subtitle assembly timed out")

    check()
    from ..services.requirements import require_python

    parser = require_python("m3u8", "Segmented subtitles")
    playlist = parser.loads(data.decode("utf-8-sig"), uri=url)
    segments = playlist.segments
    if playlist.is_variant or not playlist.is_endlist or not segments or len(segments) > MAX_SEGMENTS:
        raise ValueError("Expected bounded VOD subtitle playlist")
    # These need additional decryption/range/timeline policies, not guessed output.
    if any(segment.byterange or segment.init_section or segment.discontinuity or segment.gap_tag
           or (segment.key and segment.key.method != "NONE") for segment in segments):
        raise ValueError("Unsupported subtitle segment transport")
    urls = [validate_http_url(segment.absolute_uri) for segment in segments]
    size = len(data)
    reference = None
    mapped_segments = None
    cues, styles = {}, {}
    for target in urls:
        check()
        raw = fetch(target)
        check()
        size += len(raw)
        if size > MAX_SUBTITLE_BYTES:
            raise ValueError("Subtitle assembly exceeds byte limit")
        mapping, rows, segment_styles = _parse_segment(raw)
        if mapped_segments is not None and mapped_segments != (mapping is not None):
            raise ValueError("Inconsistent subtitle timestamp mapping")
        mapped_segments = mapping is not None
        shift = 0
        if mapping:
            if reference is None:
                reference = mapping
            # Keep the first segment's cue timeline, then unwrap clock changes.
            delta = (mapping[0] - reference[0] + _CLOCK_WRAP // 2) % _CLOCK_WRAP - _CLOCK_WRAP // 2
            shift = round(delta / 90) - (mapping[1] - reference[1])
        for start, end, settings, body, identifier in rows:
            key = (start + shift, end + shift, settings, body)
            cues.setdefault(key, identifier)
        for style in segment_styles:
            styles.setdefault(style, None)
    output = ["WEBVTT", *styles]
    for (start, end, settings, body), identifier in sorted(cues.items(), key=lambda row: row[0][:2]):
        cue = f"{_timestamp(start)} --> {_timestamp(end)}{settings}\n{body}"
        output.append(f"{identifier}\n{cue}" if identifier else cue)
    return ("\n\n".join(output) + "\n\n").encode("utf-8")
