"""Private localhost proxy and generated-content server lifecycle."""

import http.server
import re
import threading
import time
import urllib.parse
import urllib.request
import uuid

from ..services.http import SSL_CTX_SECURE
from .proxy_rules import (
    new_proxy_secret_path,
    proxy_filtered_headers,
    proxy_method_allowed,
    proxy_path_authorized,
    proxy_range_header,
    proxy_response_headers,
)
from .urls import validate_http_url

try:
    from curl_cffi import requests as cffi_requests
    CURL_CFFI_AVAILABLE = True
except ImportError:
    cffi_requests = None
    CURL_CFFI_AVAILABLE = False


_active_lock = threading.Lock()
_active_server = None
def _debug_warn(context, error):
    return None

# Fallback UA used only when the caller hasn't supplied one. Some CDNs
# validate the User-Agent shape (not just presence) against parameters
# baked into signed URLs, so a bare "Mozilla/5.0" can get rejected where a
# realistic browser UA passes.
_DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)


def configure_debug_reporter(reporter):
    global _debug_warn
    _debug_warn = reporter


class _ThreadedHTTPServer(http.server.ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False


def _is_playlist(url, content_type):
    u = url.lower()
    return (
        ".m3u8" in u
        or "master.txt" in u
        or "playlist.txt" in u
        or "mpegurl" in str(content_type or "").lower()
    )


def _upstream_content_type(hint, upstream):
    # Signed child playlists can have no recognizable extension. Their actual
    # MIME type must override a guessed segment type so nested URLs get rewritten.
    return upstream if _is_playlist("", upstream) else (hint or upstream)


def _ass_to_vtt(text: str, mpegts_pts: int = 133508) -> str:
    lines = text.splitlines()
    vtt_cues = ["WEBVTT"]
    if mpegts_pts is not None:
        vtt_cues.append(f"X-TIMESTAMP-MAP=MPEGTS:{mpegts_pts},LOCAL:00:00:00.000")
    vtt_cues.append("")

    def parse_time(t_str: str) -> str:
        parts = t_str.strip().split(":")
        if len(parts) == 3:
            h = int(parts[0])
            m = int(parts[1])
            s_parts = parts[2].split(".")
            s = int(s_parts[0])
            cs = s_parts[1] if len(s_parts) > 1 else "00"
            if len(cs) == 1:
                ms = int(cs) * 100
            elif len(cs) == 2:
                ms = int(cs) * 10
            else:
                ms = int(cs[:3])
            return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"
        return t_str

    in_events = False
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith(";"):
            continue
        if stripped.startswith("[Events]"):
            in_events = True
            continue
        if stripped.startswith("[") and in_events:
            in_events = False
            continue
        if in_events and stripped.startswith("Dialogue:"):
            parts = stripped[len("Dialogue:"):].split(",", 9)
            if len(parts) >= 10:
                start_raw = parts[1]
                end_raw = parts[2]
                raw_cue = parts[9]
                is_top = any(tag in raw_cue for tag in (r"{\an8}", r"{\an7}", r"{\an9}"))
                cue_text = re.sub(r"\{[^\}]*\}", "", raw_cue)
                cue_text = cue_text.replace(r"\N", "\n").replace(r"\n", "\n").strip()
                if not cue_text:
                    continue
                start_vtt = parse_time(start_raw)
                end_vtt = parse_time(end_raw)
                line_setting = "line:10%,start" if is_top else "line:92%,end"
                vtt_cues.append(f"{start_vtt} --> {end_vtt} {line_setting}")
                vtt_cues.append(cue_text)
                vtt_cues.append("")

    return "\n".join(vtt_cues)


def _add_vtt_padding(vtt_text: str) -> str:
    """Ensure timing cues without positioning get line:92%,end for clean Android margins."""
    def _repl(match):
        timing = match.group(0).strip()
        if "line:" not in timing:
            return f"{timing} line:92%,end"
        return timing

    return re.sub(
        r"((?:(?:\d{1,2}:)?\d{2}:\d{2}[\.,]\d{3})\s*-->\s*(?:(?:\d{1,2}:)?\d{2}:\d{2}[\.,]\d{3})[^\r\n]*)",
        _repl,
        vtt_text,
    )


def _add_vlc_vtt_padding(vtt_text: str) -> str:
    """Adjust cue vertical position for VLC so single-line is at line:88% and
    multi-line is lifted (e.g. line:83% for 2 lines) without bottom clipping.
    """
    lines = vtt_text.splitlines()
    new_lines = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if "-->" in line:
            j = i + 1
            t_count = 0
            while j < len(lines) and lines[j].strip():
                t_count += 1
                j += 1
            pos_pct = max(70, 88 - (t_count - 1) * 5)
            timing_base = line.split("line:")[0].strip()
            new_lines.append(f"{timing_base} line:{pos_pct}%")
            i += 1
        else:
            new_lines.append(line)
            i += 1
    return "\n".join(new_lines)


def _shift_vtt_timestamps(vtt_text: str, offset_seconds: float) -> str:
    """Shift WebVTT cue timestamps by offset_seconds (for players like MPV that ignore X-TIMESTAMP-MAP)."""
    if not offset_seconds:
        return vtt_text

    def _parse_time(ts: str) -> float:
        parts = ts.replace(",", ".").split(":")
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        if len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        return float(parts[0])

    def _format_time(seconds: float) -> str:
        s = max(0.0, seconds)
        h = int(s // 3600)
        m = int((s % 3600) // 60)
        rem = s % 60
        return f"{h:02d}:{m:02d}:{rem:06.3f}"

    lines = vtt_text.splitlines()
    new_lines = []
    for line in lines:
        if "-->" in line:
            parts = line.split("-->")
            s_raw = parts[0].strip()
            rest = parts[1].strip()
            e_parts = rest.split(None, 1)
            e_raw = e_parts[0]
            extra = (" " + e_parts[1]) if len(e_parts) > 1 else ""
            try:
                s_shifted = _format_time(_parse_time(s_raw) + offset_seconds)
                e_shifted = _format_time(_parse_time(e_raw) + offset_seconds)
                new_lines.append(f"{s_shifted} --> {e_shifted}{extra}")
            except Exception:
                new_lines.append(line)
        else:
            new_lines.append(line)
    return "\n".join(new_lines)


def _ensure_vtt(
    data_bytes: bytes,
    mpegts_pts: int | None = 133508,
    client_ua: str = "",
    target_player: str | None = None,
) -> bytes:
    if not data_bytes:
        return b"WEBVTT\n\n"
    if data_bytes.startswith(b"\x1f\x8b"):
        try:
            import gzip
            data_bytes = gzip.decompress(data_bytes)
        except Exception:
            pass
    text = data_bytes.decode("utf-8", errors="replace").strip()
    clean_text = text.lstrip("\ufeff")
    ua = (client_ua or "").lower()
    t_player = (target_player or "").lower()
    is_mpv = t_player in ("mpv", "rex", "mpvrx", "mpvrex") or "mpv" in ua or "lavf" in ua
    is_vlc = t_player == "vlc" or "vlc" in ua

    if "[Events]" in clean_text or "[Script Info]" in clean_text:
        pts_for_ass = None if is_mpv else mpegts_pts
        vtt = _ass_to_vtt(clean_text, mpegts_pts=pts_for_ass)
        if is_vlc:
            # Strip line:92%,end for VLC multi-line compatibility and apply VLC padding
            vtt = re.sub(r"\s+line:\d+%,end", "", vtt)
            vtt = _add_vlc_vtt_padding(vtt)
        if is_mpv and mpegts_pts:
            vtt = _shift_vtt_timestamps(vtt, mpegts_pts / 90000.0)
        return vtt.encode("utf-8")

    if clean_text.startswith("WEBVTT"):
        padded = _add_vlc_vtt_padding(clean_text) if is_vlc else _add_vtt_padding(clean_text)
        if is_mpv and mpegts_pts:
            # MPV/FFmpeg ignores X-TIMESTAMP-MAP (FFmpeg #8825); shift cues directly
            padded = _shift_vtt_timestamps(padded, mpegts_pts / 90000.0)
        elif mpegts_pts is not None and "X-TIMESTAMP-MAP" not in padded:
            # ExoPlayer (Next Player) & VLC follow RFC 8216 and parse X-TIMESTAMP-MAP
            lines = padded.splitlines()
            for idx, line in enumerate(lines):
                if line.strip().startswith("WEBVTT"):
                    lines.insert(idx + 1, f"X-TIMESTAMP-MAP=MPEGTS:{mpegts_pts},LOCAL:00:00:00.000")
                    break
            padded = "\n".join(lines)
        return padded.encode("utf-8")

    # If it's an SRT subtitle or text cues, convert commas to dots and prepend WEBWTT
    clean_text = re.sub(r"(\d{1,2}:\d{2}:\d{2}),(\d{3})", r"\1.\2", clean_text)
    clean_text = re.sub(r"(\d{2}:\d{2}),(\d{3})", r"\1.\2", clean_text)
    clean_text = _add_vlc_vtt_padding(clean_text) if is_vlc else _add_vtt_padding(clean_text)

    if is_mpv and mpegts_pts:
        clean_text = _shift_vtt_timestamps(clean_text, mpegts_pts / 90000.0)
        return f"WEBVTT\n\n{clean_text}\n".encode("utf-8")

    header = ["WEBVTT"]
    if mpegts_pts is not None:
        header.append(f"X-TIMESTAMP-MAP=MPEGTS:{mpegts_pts},LOCAL:00:00:00.000")
    header.append("")
    return ("\n".join(header) + "\n" + clean_text + "\n").encode("utf-8")



def _prepare_subtitle_entries(subtitles):
    """Normalize, deduplicate, and prepare subtitle entries.
    If a language has both .m3u8 and .srt/.vtt, prefer .m3u8.
    """
    lang_codes = {
        "en": "en", "eng": "en", "english": "en",
        "es": "es", "spa": "es", "spanish": "es", "español": "es", "espanol": "es",
        "pt": "pt", "por": "pt", "portuguese": "pt", "português": "pt", "portugues": "pt",
        "fr": "fr", "fre": "fr", "fra": "fr", "french": "fr", "français": "fr", "francais": "fr",
        "de": "de", "ger": "de", "deu": "de", "german": "de", "deutsch": "de",
        "it": "it", "ita": "it", "italian": "it", "italiano": "it",
        "ru": "ru", "rus": "ru", "russian": "ru",
        "ar": "ar", "ara": "ar", "arabic": "ar",
        "tr": "tr", "tur": "tr", "turkish": "tr", "türkçe": "tr", "turkce": "tr",
        "pl": "pl", "pol": "pl", "polish": "pl", "polski": "pl",
        "id": "id", "ind": "id", "indonesian": "id", "indonesia": "id", "bahasa": "id",
        "vi": "vi", "vie": "vi", "vietnamese": "vi",
        "th": "th", "tha": "th", "thai": "th",
        "zh": "zh", "chi": "zh", "zho": "zh", "chinese": "zh",
        "ja": "ja", "jpn": "ja", "japanese": "ja",
        "ko": "ko", "kor": "ko", "korean": "ko",
    }
    deduped = {}
    for s in (subtitles or []):
        url = s.get("url") or s.get("file") if isinstance(s, dict) else s[3] if isinstance(s, (list, tuple)) and len(s) > 3 else None
        if not url:
            continue
        label = s.get("label", "Sub") if isinstance(s, dict) else s[0] if isinstance(s, (list, tuple)) else "Sub"
        def_flag = s.get("default", False) if isinstance(s, dict) else s[2] if isinstance(s, (list, tuple)) and len(s) > 2 else False
        clean_label = str(label).split("(")[0].strip() or "English"
        norm_key = re.sub(r"[^a-zA-Z0-9]", "", clean_label.lower())
        is_m3u8 = ".m3u8" in url.lower()
        if norm_key not in deduped or (is_m3u8 and not deduped[norm_key]["is_m3u8"]):
            clean_lower = clean_label.lower()
            lang_code = "en" if ("eng" in clean_lower or "en" in clean_lower) else "und"
            for k, code in lang_codes.items():
                if k in clean_lower:
                    lang_code = code
                    break
            deduped[norm_key] = {
                "url": url,
                "label": clean_label,
                "lang": lang_code,
                "default": def_flag,
                "is_m3u8": is_m3u8,
            }

    if deduped and not any(d["default"] for d in deduped.values()):
        eng_key = next((k for k, v in deduped.items() if v["lang"] == "en"), None)
        if eng_key:
            deduped[eng_key]["default"] = True
        else:
            first_key = next(iter(deduped))
            deduped[first_key]["default"] = True

    return list(deduped.values())


def _guess_ext(url):
    path = urllib.parse.urlsplit(url).path
    name = path.rsplit("/", 1)[-1].lower()
    if "." in name:
        ext = name.rsplit(".", 1)[-1].lower()
        if ext in ("m3u8", "vtt", "mp4", "m4s", "aac", "mp3", "ts"):
            return ext
        if "master" in name or "playlist" in name:
            return "m3u8"
    return "m3u8" if (".m3u8" in url.lower() or "master.txt" in url.lower() or "playlist.txt" in url.lower()) else "vtt" if ".vtt" in url.lower() else "ts"


def _detect_fake_media_header(data: bytes) -> int:
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return 12
    if len(data) >= 8 and data[:8] == b"\x89PNG\r\n\x1a\n":
        iend_pos = data.find(b"IEND")
        if iend_pos != -1 and len(data) > iend_pos + 8:
            return iend_pos + 8
        if len(data) > 252:
            return 252
        return 8
    return 0


def _xor_bytes(data: bytes, mask: list[int], start_index: int = 0) -> bytes:
    if not data or not mask:
        return data
    out = bytearray(data)
    mask_len = len(mask)
    for idx, value in enumerate(out):
        out[idx] = value ^ (int(mask[(start_index + idx) % mask_len]) & 255)
    return bytes(out)


def _flixcloud_ensure_token(segment_url: str, parent_url: str) -> str:
    try:
        seg = urllib.parse.urlsplit(segment_url)
        seg_qs = urllib.parse.parse_qs(seg.query)
        if seg_qs.get("token"):
            return segment_url

        token = None
        current = parent_url
        for _ in range(3):
            parsed = urllib.parse.urlsplit(current)
            qs = urllib.parse.parse_qs(parsed.query)
            token_vals = qs.get("token")
            if token_vals:
                token = token_vals[0]
                break
            nested_vals = qs.get("url")
            if not nested_vals:
                break
            current = nested_vals[0]
        if not token:
            return segment_url

        query = urllib.parse.urlencode({**{k: v[-1] for k, v in seg_qs.items()}, "token": token})
        return urllib.parse.urlunsplit((seg.scheme, seg.netloc, seg.path, query, seg.fragment))
    except Exception:
        return segment_url


def _build_proxy_server(initial_entries, timeout, show_id=None, episode=None, duration=0.0, on_progress=None):
    """Spin up one local proxy server backing an arbitrary set of routes.

    initial_entries: {path: entry_dict}, where entry_dict is either
      {"kind": "fetch", "url": ..., "ref": ..., "hdrs": {...}}
      {"kind": "synthetic", "text": "...m3u8 content..."}

    Returns (port, registry, register_fn, server). register_fn lets the
    handler add newly-discovered child routes (segments, sub-playlists,
    keys) on the fly while rewriting a playlist it just fetched.
    """
    registry = dict(initial_entries)
    registry_lock = threading.Lock()
    port_holder = {}
    progress_state = {
        "last_saved_pos": -1,
        "last_save_time": 0.0,
        "duration": float(duration or 0.0),
    }

    def register(url, ref, hdrs, content_type=None, time_offset=None, extra=None):
        ext = _guess_ext(url)
        path = new_proxy_secret_path(ext)
        entry_content_type = content_type
        if not entry_content_type:
            if ext == "m3u8":
                entry_content_type = "application/vnd.apple.mpegurl"
            elif ext == "vtt":
                entry_content_type = "text/vtt; charset=utf-8"
            elif ext == "ts":
                entry_content_type = "video/MP2T"
            elif ext in ("m4s", "mp4"):
                entry_content_type = "video/mp4"
            elif ext in ("aac", "m4a", "mp3"):
                entry_content_type = "audio/mp4"
        with registry_lock:
            registry[path] = {
                "kind": "fetch",
                "url": url,
                "ref": ref,
                "hdrs": dict(hdrs),
                "content_type": entry_content_type,
            }
            if extra:
                registry[path].update(extra)
            if time_offset is not None:
                registry[path]["time_offset"] = float(time_offset)
        return path

    def local_url_for(path):
        return path

    def rewrite_playlist(text, base_url, ref, hdrs, entry_options=None):
        entry_options = entry_options or {}
        if text.startswith("EM3U8v1:"):
            from .decryption import decrypt_em3u8
            dec = decrypt_em3u8(text)
            if dec:
                text = dec
        base = base_url.rsplit("/", 1)[0] + "/"
        out = []
        running_time = 0.0
        pending_duration = None
        for line in text.splitlines():
            stripped = line.strip()

            if stripped.startswith("#EXTINF:"):
                try:
                    m = re.match(r"#EXTINF:\s*([0-9.]+)", stripped)
                    if m:
                        pending_duration = float(m.group(1))
                except Exception:
                    pending_duration = None
                out.append(line)
                continue

            if stripped.startswith("#EXT-X-STREAM-INF:"):
                # Clean deprecated tags like PROGRAM-ID
                line = re.sub(r"PROGRAM-ID=\d+,?", "", line)
                line = re.sub(r",+", ",", line).replace(":,", ":").rstrip(",")
                out.append(line)
                continue

            if stripped.startswith("#") and 'URI="' in stripped:
                start = line.index('URI="') + 5
                end = line.index('"', start)
                target = line[start:end]
                real = urllib.parse.urljoin(base, target)
                extra = {}
                if entry_options.get("flixcloud_w_payload"):
                    real = _flixcloud_ensure_token(real, base_url)
                    extra = {
                        "flixcloud_w_payload": entry_options.get("flixcloud_w_payload"),
                        "flixcloud_xor_mask": entry_options.get("flixcloud_xor_mask"),
                    }
                path = register(real, ref, hdrs, extra=extra)
                line = line[:start] + local_url_for(path) + line[end:]
                out.append(line)
                continue

            if stripped.startswith("#") or not stripped:
                out.append(line)
                continue

            real = urllib.parse.urljoin(base, stripped)
            extra = {}
            if entry_options.get("flixcloud_w_payload"):
                real = _flixcloud_ensure_token(real, base_url)
                extra = {
                    "flixcloud_w_payload": entry_options.get("flixcloud_w_payload"),
                    "flixcloud_xor_mask": entry_options.get("flixcloud_xor_mask"),
                }
            if pending_duration is not None:
                path = register(real, ref, hdrs, time_offset=running_time, extra=extra)
                running_time += pending_duration
                pending_duration = None
            else:
                path = register(real, ref, hdrs, extra=extra)
            out.append(local_url_for(path))

        if running_time > 0:
            progress_state["duration"] = max(progress_state.get("duration", 0.0), running_time)

        return "\n".join(out)


    class ProxyHandler(http.server.BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def _reject_method(self):
            self.send_response(405)
            self.send_header("Allow", "GET, HEAD")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _proxy(self, method):
            if not proxy_method_allowed(method):
                self._reject_method()
                return

            path = urllib.parse.urlsplit(self.path).path
            with registry_lock:
                entry = registry.get(path)
            if entry is None:
                try:
                    self.send_error(404)
                except Exception:
                    pass
                return

            if method == "GET" and show_id is not None and episode is not None and entry.get("time_offset") is not None:
                t_pos = max(0, int(entry["time_offset"]) - 20)
                if t_pos >= 10:
                    now = time.time()
                    if abs(t_pos - progress_state["last_saved_pos"]) >= 5 or (now - progress_state["last_save_time"]) >= 5.0:
                        progress_state["last_saved_pos"] = t_pos
                        progress_state["last_save_time"] = now
                        total_dur = progress_state.get("duration", 0.0)
                        is_complete = False
                        if total_dur > 300:
                            if t_pos >= (0.80 * total_dur) or (total_dur - t_pos) <= 150:
                                is_complete = True
                        elif total_dur > 60:
                            if t_pos >= (0.85 * total_dur):
                                is_complete = True

                        if on_progress:
                            try:
                                on_progress(show_id, episode, t_pos, total_dur, is_complete)
                            except Exception as err:
                                _debug_warn("on_progress callback failed", err)
                        else:
                            try:
                                from ..core.storage import save_resume_time
                                if is_complete:
                                    save_resume_time(show_id, episode, 0)
                                else:
                                    save_resume_time(show_id, episode, t_pos)
                            except Exception as err:
                                _debug_warn("Failed to save resume progress in local proxy", err)

            if entry["kind"] == "synthetic":
                data = entry["text"].encode("utf-8")
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", entry.get("content_type", "application/vnd.apple.mpegurl"))
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.send_header("Access-Control-Allow-Headers", "*")
                    self.send_header("Cache-Control", "no-store, no-cache")
                    self.end_headers()
                    if method == "GET":
                        self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError):
                    pass
                return

            url, ref, hdrs = entry["url"], entry["ref"], entry["hdrs"]
            range_header = proxy_range_header(self.headers.get("Range", ""))
            is_m3u8_fetch = _is_playlist(url, entry.get("content_type", ""))

            # Try upstream fetch via urllib first (primary, standard, transparent)
            try:
                request = urllib.request.Request(url, method=method)
                request.add_header("User-Agent", hdrs.get("User-Agent", _DEFAULT_UA))
                if ref:
                    request.add_header("Referer", ref)
                    if "Origin" not in hdrs and "origin" not in hdrs:
                        parsed_ref = urllib.parse.urlparse(ref)
                        request.add_header("Origin", f"{parsed_ref.scheme}://{parsed_ref.netloc}")
                for key, value in hdrs.items():
                    if key.casefold() == "user-agent":
                        continue
                    request.add_header(key, value)
                if range_header and not is_m3u8_fetch:
                    request.add_header("Range", range_header)

                with urllib.request.urlopen(
                        request,
                        context=SSL_CTX_SECURE,
                        timeout=max(1, float(timeout))) as response:
                    validate_http_url(response.geturl())
                    content_type = _upstream_content_type(entry.get("content_type"), response.headers.get("Content-Type", ""))

                    if method == "GET" and _is_playlist(url, content_type):
                        body = response.read()
                        text = body.decode("utf-8", errors="replace")
                        rewritten = rewrite_playlist(text, url, ref, hdrs, entry)
                        data = rewritten.encode("utf-8")
                        try:
                            self.send_response(200)
                            self.send_header("Content-Type", "application/vnd.apple.mpegurl")
                            self.send_header("Content-Length", str(len(data)))
                            self.send_header("Access-Control-Allow-Origin", "*")
                            self.send_header("Access-Control-Allow-Headers", "*")
                            self.send_header("Cache-Control", "no-store, no-cache")
                            self.end_headers()
                            self.wfile.write(data)
                        except (BrokenPipeError, ConnectionResetError):
                            pass
                        return

                    is_vtt = (
                        "vtt" in str(entry.get("content_type") or "").lower()
                        or "vtt" in str(content_type or "").lower()
                        or url.lower().endswith(".vtt")
                        or url.lower().endswith(".srt")
                    )
                    if method == "GET" and is_vtt:
                        raw_data = response.read()
                        client_ua = self.headers.get("User-Agent", "")
                        pts = entry.get("mpegts_pts", 133508 if entry.get("is_hls", True) else None)
                        target_player = entry.get("target_player")
                        vtt_data = _ensure_vtt(
                            raw_data,
                            mpegts_pts=pts,
                            client_ua=client_ua,
                            target_player=target_player,
                        )
                        try:
                            self.send_response(200)
                            self.send_header("Content-Type", "text/vtt; charset=utf-8")
                            self.send_header("Content-Length", str(len(vtt_data)))
                            self.send_header("Access-Control-Allow-Origin", "*")
                            self.send_header("Access-Control-Allow-Headers", "*")
                            self.send_header("Cache-Control", "no-store, no-cache")
                            self.end_headers()
                            self.wfile.write(vtt_data)
                        except (BrokenPipeError, ConnectionResetError):
                            pass
                        return

                    try:
                        first_chunk = b""
                        if method == "GET":
                            first_chunk = response.read(65536)
                            while first_chunk.startswith(b"\x89PNG\r\n\x1a\n") and len(first_chunk) < 253:
                                more = response.read(253 - len(first_chunk))
                                if not more:
                                    break
                                first_chunk += more

                        fake_header_size = _detect_fake_media_header(first_chunk)
                        xor_mask = entry.get("flixcloud_xor_mask") or []
                        should_xor = False
                        if fake_header_size > 0:
                            payload_probe = first_chunk[fake_header_size:fake_header_size + 1]
                            if payload_probe:
                                should_xor = bool(xor_mask) and ((payload_probe[0] ^ (int(xor_mask[0]) & 255)) == 0x47)
                            first_chunk = first_chunk[fake_header_size:]
                        if should_xor:
                            first_chunk = _xor_bytes(first_chunk, xor_mask, 0)
                        xor_index = len(first_chunk) if should_xor else 0

                        self.send_response(response.status)
                        content_type_sent = False
                        has_accept_ranges = False
                        for key, value in proxy_response_headers(response.headers):
                            k_low = key.lower()
                            if k_low == "accept-ranges":
                                has_accept_ranges = True
                            elif k_low == "content-length":
                                if fake_header_size or should_xor:
                                    continue
                            elif k_low == "content-type":
                                if fake_header_size or should_xor:
                                    value = "video/MP2T"
                                elif entry.get("content_type"):
                                    value = entry["content_type"]
                                elif not value.startswith("video/") and not value.startswith("audio/"):
                                    value = "video/MP2T"
                                content_type_sent = True
                            self.send_header(key, value)
                        if not content_type_sent:
                            self.send_header("Content-Type", "video/MP2T" if (fake_header_size or should_xor) else entry.get("content_type", "video/MP2T"))
                        if not has_accept_ranges and response.status == 200:
                            self.send_header("Accept-Ranges", "bytes")
                        self.send_header("Access-Control-Allow-Origin", "*")
                        self.send_header("Access-Control-Allow-Headers", "*")
                        self.end_headers()
                        if method == "GET":
                            if first_chunk:
                                self.wfile.write(first_chunk)
                            while chunk := response.read(65536):
                                if should_xor:
                                    chunk = _xor_bytes(chunk, xor_mask, xor_index)
                                    xor_index += len(chunk)
                                try:
                                    self.wfile.write(chunk)
                                except (BrokenPipeError, ConnectionResetError):
                                    break
                    except (BrokenPipeError, ConnectionResetError):
                        pass
                    return
            except (BrokenPipeError, ConnectionResetError):
                return
            except urllib.error.HTTPError as http_err:
                # Upstream returned 403; fallback to curl_cffi if available (Cloudflare bypass)
                if http_err.code == 403 and CURL_CFFI_AVAILABLE and cffi_requests is not None:
                    try:
                        cffi_hdrs = dict(hdrs) if hdrs else {}
                        if "User-Agent" not in cffi_hdrs and "user-agent" not in cffi_hdrs:
                            cffi_hdrs["User-Agent"] = _DEFAULT_UA
                        if ref:
                            if "Referer" not in cffi_hdrs and "referer" not in cffi_hdrs:
                                cffi_hdrs["Referer"] = ref
                            if "Origin" not in cffi_hdrs and "origin" not in cffi_hdrs:
                                parsed_ref = urllib.parse.urlparse(ref)
                                cffi_hdrs["Origin"] = f"{parsed_ref.scheme}://{parsed_ref.netloc}"
                        if range_header and not is_m3u8_fetch:
                            cffi_hdrs["Range"] = range_header

                        imp = "firefox147" if "Firefox" in cffi_hdrs.get("User-Agent", "") else "chrome"
                        resp = cffi_requests.request(
                            method,
                            url,
                            headers=cffi_hdrs,
                            timeout=max(1, float(timeout)),
                            impersonate=imp,
                            verify=False,
                            stream=True,
                        )
                        content_type = _upstream_content_type(
                            entry.get("content_type"), resp.headers.get("Content-Type") or resp.headers.get("content-type", ""),
                        )

                        if method == "GET" and _is_playlist(url, content_type):
                            raw_chunks = []
                            for chunk in resp.iter_content(65536):
                                raw_chunks.append(chunk)
                            body = b"".join(raw_chunks)
                            text = body.decode("utf-8", errors="replace")
                            rewritten = rewrite_playlist(text, url, ref, hdrs, entry)
                            data = rewritten.encode("utf-8")
                            try:
                                self.send_response(200)
                                self.send_header("Content-Type", "application/vnd.apple.mpegurl")
                                self.send_header("Content-Length", str(len(data)))
                                self.send_header("Access-Control-Allow-Origin", "*")
                                self.send_header("Access-Control-Allow-Headers", "*")
                                self.send_header("Cache-Control", "no-store, no-cache")
                                self.end_headers()
                                self.wfile.write(data)
                            except (BrokenPipeError, ConnectionResetError):
                                pass
                            return

                        is_vtt = (
                            "vtt" in str(entry.get("content_type") or "").lower()
                            or "vtt" in str(content_type or "").lower()
                            or url.lower().endswith(".vtt")
                            or url.lower().endswith(".srt")
                        )
                        if method == "GET" and is_vtt:
                            raw_chunks = []
                            for chunk in resp.iter_content(65536):
                                raw_chunks.append(chunk)
                            client_ua = self.headers.get("User-Agent", "")
                            pts = entry.get("mpegts_pts", 133508 if entry.get("is_hls", True) else None)
                            target_player = entry.get("target_player")
                            vtt_data = _ensure_vtt(
                                b"".join(raw_chunks),
                                mpegts_pts=pts,
                                client_ua=client_ua,
                                target_player=target_player,
                            )
                            try:
                                self.send_response(200)
                                self.send_header("Content-Type", "text/vtt; charset=utf-8")
                                self.send_header("Content-Length", str(len(vtt_data)))
                                self.send_header("Access-Control-Allow-Origin", "*")
                                self.send_header("Access-Control-Allow-Headers", "*")
                                self.send_header("Cache-Control", "no-store, no-cache")
                                self.end_headers()
                                self.wfile.write(vtt_data)
                            except (BrokenPipeError, ConnectionResetError):
                                pass
                            return

                        try:
                            first_chunk = b""
                            resp_iter = None
                            if method == "GET":
                                resp_iter = resp.iter_content(65536)
                                try:
                                    first_chunk = next(resp_iter)
                                except StopIteration:
                                    first_chunk = b""
                                while first_chunk.startswith(b"\x89PNG\r\n\x1a\n") and len(first_chunk) < 253:
                                    try:
                                        first_chunk += next(resp_iter)
                                    except StopIteration:
                                        break

                            fake_header_size = _detect_fake_media_header(first_chunk)
                            xor_mask = entry.get("flixcloud_xor_mask") or []
                            should_xor = False
                            if fake_header_size > 0:
                                payload_probe = first_chunk[fake_header_size:fake_header_size + 1]
                                if payload_probe:
                                    should_xor = bool(xor_mask) and ((payload_probe[0] ^ (int(xor_mask[0]) & 255)) == 0x47)
                                first_chunk = first_chunk[fake_header_size:]
                            if should_xor:
                                first_chunk = _xor_bytes(first_chunk, xor_mask, 0)
                            xor_index = len(first_chunk) if should_xor else 0

                            self.send_response(resp.status_code)
                            content_type_sent = False
                            has_accept_ranges = False
                            for key, value in resp.headers.items():
                                k_low = key.lower()
                                if k_low in ("connection", "keep-alive", "transfer-encoding", "trailer", "upgrade", "content-encoding"):
                                    continue
                                if k_low == "accept-ranges":
                                    has_accept_ranges = True
                                elif k_low == "content-length":
                                    if fake_header_size or should_xor:
                                        continue
                                elif k_low == "content-type":
                                    if fake_header_size or should_xor:
                                        value = "video/MP2T"
                                    elif entry.get("content_type"):
                                        value = entry["content_type"]
                                    elif not value.startswith("video/") and not value.startswith("audio/"):
                                        value = "video/MP2T"
                                    content_type_sent = True
                                self.send_header(key, value)
                            if not content_type_sent:
                                self.send_header(
                                    "Content-Type",
                                    "video/MP2T" if (fake_header_size or should_xor) else (entry.get("content_type") or "video/MP2T"),
                                )
                            if not has_accept_ranges and resp.status_code == 200:
                                self.send_header("Accept-Ranges", "bytes")
                            self.send_header("Access-Control-Allow-Origin", "*")
                            self.send_header("Access-Control-Allow-Headers", "*")
                            self.end_headers()
                            if method == "GET":
                                if first_chunk:
                                    self.wfile.write(first_chunk)
                                if resp_iter:
                                    for chunk in resp_iter:
                                        if should_xor:
                                            chunk = _xor_bytes(chunk, xor_mask, xor_index)
                                            xor_index += len(chunk)
                                        try:
                                            self.wfile.write(chunk)
                                        except (BrokenPipeError, ConnectionResetError):
                                            break
                        except (BrokenPipeError, ConnectionResetError):
                            pass
                        return
                    except (BrokenPipeError, ConnectionResetError):
                        return
                    except Exception as cffi_exc:
                        _debug_warn("curl_cffi local proxy fallback failed", cffi_exc)
                _debug_warn("Local proxy upstream request failed", http_err)
                try:
                    self.send_error(502, "Upstream stream unavailable")
                except Exception:
                    pass
            except Exception as exc:
                _debug_warn("Local proxy upstream request failed", exc)
                try:
                    self.send_error(502, "Upstream stream unavailable")
                except Exception:
                    pass

        def do_GET(self):
            self._proxy("GET")

        def do_HEAD(self):
            self._proxy("HEAD")

        def do_OPTIONS(self):
            self.send_response(200)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "*")
            self.send_header("Content-Length", "0")
            self.end_headers()

        do_POST = _reject_method
        do_PUT = _reject_method
        do_DELETE = _reject_method
        do_PATCH = _reject_method

    server = _ThreadedHTTPServer(("0.0.0.0", 0), ProxyHandler)
    port = server.server_address[1]
    port_holder["port"] = port
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return port, registry, register, server


def start_local_proxy(
        target_url, referer, headers=None, timeout=15,
        stream_type="mp4", title="stream", subtitles=None,
        target_player=None, show_id=None, episode=None,
        duration=0.0, on_progress=None,
        flixcloud_w_payload=None, flixcloud_xor_mask=None):
    validate_http_url(target_url)
    is_hls = str(stream_type).lower() == "hls"
    extension = "m3u8" if is_hls else "mp4"
    base_secret = new_proxy_secret_path(extension, title=title)
    forwarded_headers = proxy_filtered_headers(headers)

    target_fetch_url = target_url
    hls_res_filter = None
    if "hls_res=" in target_url:
        m_res = re.search(r"[?&]hls_res=(\d+p?)", target_url)
        if m_res:
            hls_res_filter = m_res.group(1).rstrip("p")
        target_fetch_url = re.sub(r"[?&]hls_res=[^&]+", "", target_url)
        if "?" not in target_fetch_url and "&" in target_fetch_url:
            target_fetch_url = target_fetch_url.replace("&", "?", 1)

    # If HLS and (subtitles or resolution filter) are provided, generate synthetic master M3U8
    if is_hls and (subtitles or hls_res_filter):
        master_secret = base_secret
        video_secret = new_proxy_secret_path("m3u8")

        initial = {
            master_secret: {"kind": "synthetic", "text": ""},
            video_secret: {
                "kind": "fetch",
                "url": target_fetch_url,
                "ref": referer,
                "hdrs": dict(forwarded_headers),
                "flixcloud_w_payload": flixcloud_w_payload,
                "flixcloud_xor_mask": flixcloud_xor_mask,
            },
        }

        sub_list = _prepare_subtitle_entries(subtitles) if subtitles else []
        sub_entries = []
        for s in sub_list:
            if s["is_m3u8"]:
                sub_entries.append({
                    "name": s["label"],
                    "lang": s["lang"],
                    "default": "YES" if s["default"] else "NO",
                    "is_m3u8": True,
                    "url": s["url"],
                })
            else:
                vtt_secret = new_proxy_secret_path("vtt", title=s["label"])
                m3u8_secret = new_proxy_secret_path("m3u8", title=s["label"])
                initial[vtt_secret] = {
                    "kind": "fetch",
                    "url": s["url"],
                    "ref": referer,
                    "hdrs": dict(forwarded_headers),
                    "content_type": "text/vtt; charset=utf-8",
                    "mpegts_pts": 133508 if is_hls else None,
                    "is_hls": is_hls,
                    "target_player": target_player,
                }
                initial[m3u8_secret] = {
                    "kind": "synthetic",
                    "text": "",
                }
                sub_entries.append({
                    "name": s["label"],
                    "lang": s["lang"],
                    "default": "YES" if s["default"] else "NO",
                    "is_m3u8": False,
                    "m3u8_secret": m3u8_secret,
                    "vtt_secret": vtt_secret,
                })

        port, registry, _register, server = _build_proxy_server(
            initial, timeout, show_id=show_id, episode=episode,
            duration=duration, on_progress=on_progress
        )

        # Build Master M3U8 content
        master_lines = ["#EXTM3U", "#EXT-X-VERSION:3", ""]
        for sub in sub_entries:
            if sub["is_m3u8"]:
                sub_path = _register(sub["url"], referer, forwarded_headers)
                sub_m3u8_url = f"http://127.0.0.1:{port}{sub_path}"
            else:
                sub_m3u8_url = f"http://127.0.0.1:{port}{sub['m3u8_secret']}"
                sub_vtt_url = f"http://127.0.0.1:{port}{sub['vtt_secret']}"
                registry[sub["m3u8_secret"]]["text"] = (
                    f"#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:1500\n"
                    f"#EXT-X-MEDIA-SEQUENCE:1\n#EXT-X-PLAYLIST-TYPE:VOD\n#EXTINF:1500.0,\n"
                    f"{sub_vtt_url}\n#EXT-X-ENDLIST\n"
                )

            master_lines.append(
                f'#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="subs",NAME="{sub["name"]}",'
                f'LANGUAGE="{sub["lang"]}",DEFAULT={sub["default"]},AUTOSELECT={sub["default"]},FORCED=NO,URI="{sub_m3u8_url}"'
            )

        if sub_entries:
            master_lines.append("")

        # Inspect target_url to see if it's already a master or a single stream
        video_local_url = f"http://127.0.0.1:{port}{video_secret}"

        try:
            req = urllib.request.Request(target_fetch_url)
            req.add_header("User-Agent", forwarded_headers.get("User-Agent", _DEFAULT_UA))
            if referer:
                req.add_header("Referer", referer)
                if "Origin" not in forwarded_headers and "origin" not in forwarded_headers:
                    parsed_ref = urllib.parse.urlparse(referer)
                    req.add_header("Origin", f"{parsed_ref.scheme}://{parsed_ref.netloc}")
            for key, value in forwarded_headers.items():
                if key.casefold() in ("user-agent", "referer"):
                    continue
                req.add_header(key, value)
            with urllib.request.urlopen(req, context=SSL_CTX_SECURE, timeout=max(1, float(timeout))) as resp:
                body = resp.read().decode("utf-8", errors="replace")

            if body.startswith("EM3U8v1:"):
                from .decryption import decrypt_em3u8
                dec = decrypt_em3u8(body)
                if dec:
                    body = dec

            if "#EXT-X-STREAM-INF" in body:
                base_target = target_fetch_url.rsplit("/", 1)[0] + "/"
                pending_inf = None
                skip_next_var = False
                for line in body.splitlines():
                    stripped = line.strip()
                    if not stripped:
                        continue
                    if stripped.startswith("#EXT-X-MEDIA:TYPE=AUDIO"):
                        if 'URI="' in stripped:
                            u_start = stripped.index('URI="') + 5
                            u_end = stripped.index('"', u_start)
                            raw_a_uri = stripped[u_start:u_end]
                            abs_a_uri = urllib.parse.urljoin(base_target, raw_a_uri)
                            a_path = _register(abs_a_uri, referer, forwarded_headers)
                            stripped = stripped[:u_start] + f"http://127.0.0.1:{port}{a_path}" + stripped[u_end:]
                        master_lines.append(stripped)
                        continue
                    if stripped.startswith("#EXT-X-STREAM-INF:"):
                        if hls_res_filter:
                            res_m = re.search(r"RESOLUTION=\d+x(\d+)", stripped)
                            if res_m and res_m.group(1) != hls_res_filter:
                                pending_inf = None
                                skip_next_var = True
                                continue
                        skip_next_var = False
                        if sub_entries:
                            if 'SUBTITLES="' in stripped:
                                pending_inf = re.sub(r'SUBTITLES="[^"]*"', 'SUBTITLES="subs"', stripped)
                            else:
                                pending_inf = f'{stripped},SUBTITLES="subs"'
                        else:
                            pending_inf = stripped
                        master_lines.append(pending_inf)
                        continue
                    if stripped.startswith("#"):
                        if not stripped.startswith("#EXTM3U") and not stripped.startswith("#EXT-X-VERSION"):
                            master_lines.append(stripped)
                        continue
                    if pending_inf is not None and not skip_next_var:
                        var_abs = urllib.parse.urljoin(base_target, stripped)
                        extra = None
                        if flixcloud_w_payload:
                            var_abs = _flixcloud_ensure_token(var_abs, target_fetch_url)
                            extra = {
                                "flixcloud_w_payload": flixcloud_w_payload,
                                "flixcloud_xor_mask": flixcloud_xor_mask,
                            }
                        var_path = _register(var_abs, referer, forwarded_headers, extra=extra)
                        master_lines.append(f"http://127.0.0.1:{port}{var_path}")
                        pending_inf = None
            else:
                if sub_entries:
                    master_lines.append('#EXT-X-STREAM-INF:BANDWIDTH=2889119,RESOLUTION=1920x1080,SUBTITLES="subs"')
                else:
                    master_lines.append('#EXT-X-STREAM-INF:BANDWIDTH=2889119,RESOLUTION=1920x1080')
                master_lines.append(video_local_url)
        except Exception:
            if sub_entries:
                master_lines.append('#EXT-X-STREAM-INF:BANDWIDTH=2889119,RESOLUTION=1920x1080,SUBTITLES="subs"')
            else:
                master_lines.append('#EXT-X-STREAM-INF:BANDWIDTH=2889119,RESOLUTION=1920x1080')
            master_lines.append(video_local_url)

        master_lines.append("")
        registry[master_secret]["text"] = "\n".join(master_lines)
        return f"http://127.0.0.1:{port}{master_secret}", server

    initial = {
        base_secret: {
            "kind": "fetch",
            "url": target_fetch_url,
            "ref": referer,
            "hdrs": dict(forwarded_headers),
            "flixcloud_w_payload": flixcloud_w_payload,
            "flixcloud_xor_mask": flixcloud_xor_mask,
        }
    }
    port, _registry, _register, server = _build_proxy_server(
        initial, timeout, show_id=show_id, episode=episode,
        duration=duration, on_progress=on_progress
    )
    return f"http://127.0.0.1:{port}{base_secret}", server



def start_local_dual_proxy(
        video_url, audio_url, referer, headers=None, timeout=15,
        width=1280, height=720, bandwidth=2_400_000, title="stream",
        subtitles=None, audio_tracks=None, target_player=None,
        show_id=None, episode=None, duration=0.0, on_progress=None,
        flixcloud_w_payload=None, flixcloud_xor_mask=None):
    """Like start_local_proxy, but for sources that split video and audio
    into two separate HLS manifests (Dailymotion does this) with no
    combined master. Builds the master ourselves; both sub-manifests go
    through the SAME rewriting/header machinery as everything else, so
    Dailymotion's video track gets the same header treatment its audio
    track does, instead of being served as a static unproxied file.
    """
    validate_http_url(video_url)
    if audio_url:
        validate_http_url(audio_url)
    forwarded_headers = proxy_filtered_headers(headers)

    master_secret = new_proxy_secret_path("m3u8", title=title)
    video_secret = new_proxy_secret_path("m3u8")

    tracks_to_use = list(audio_tracks) if audio_tracks else []
    if not tracks_to_use and audio_url:
        tracks_to_use = [{"url": audio_url, "label": "Audio", "default": True}]

    has_default = any(t.get("default") for t in tracks_to_use)
    if not has_default and tracks_to_use:
        tracks_to_use[0]["default"] = True

    media_extra = {}
    if flixcloud_w_payload:
        media_extra = {
            "flixcloud_w_payload": flixcloud_w_payload,
            "flixcloud_xor_mask": flixcloud_xor_mask,
        }

    initial = {
        master_secret: {"kind": "synthetic", "text": ""},  # filled in below
        video_secret: {
            "kind": "fetch", "url": video_url, "ref": referer,
            "hdrs": dict(forwarded_headers),
            **media_extra,
        },
    }

    audio_secrets = []
    audio_lines = []
    for idx, track in enumerate(tracks_to_use):
        t_url = track.get("url")
        if not t_url:
            continue
        try:
            validate_http_url(t_url)
        except ValueError:
            continue
        a_secret = new_proxy_secret_path("m3u8")
        audio_secrets.append((a_secret, idx))
        initial[a_secret] = {
            "kind": "fetch", "url": t_url, "ref": referer,
            "hdrs": dict(forwarded_headers),
            **media_extra,
        }
        t_name = track.get("label") or track.get("name") or f"Audio {idx+1}"
        t_lang = track.get("language") or ""
        lang_attr = f',LANGUAGE="{t_lang}"' if t_lang else ""
        is_def = "YES" if track.get("default") else "NO"
        audio_lines.append(
            f'#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="audio",NAME="{t_name}",'
            f'DEFAULT={is_def},AUTOSELECT={is_def}{lang_attr},URI="__AUDIO_URL_{idx}__"'
        )

    sub_list = _prepare_subtitle_entries(subtitles)
    sub_entries = []
    for s in sub_list:
        if s["is_m3u8"]:
            sub_entries.append({
                "name": s["label"],
                "lang": s["lang"],
                "default": "YES" if s["default"] else "NO",
                "is_m3u8": True,
                "url": s["url"],
            })
        else:
            vtt_secret = new_proxy_secret_path("vtt", title=s["label"])
            m3u8_secret = new_proxy_secret_path("m3u8", title=s["label"])
            initial[vtt_secret] = {
                "kind": "fetch",
                "url": s["url"],
                "ref": referer,
                "hdrs": dict(forwarded_headers),
                "content_type": "text/vtt; charset=utf-8",
                "mpegts_pts": 133508,
                "is_hls": True,
                "target_player": target_player,
            }
            initial[m3u8_secret] = {
                "kind": "synthetic",
                "text": "",
            }
            sub_entries.append({
                "name": s["label"],
                "lang": s["lang"],
                "default": "YES" if s["default"] else "NO",
                "is_m3u8": False,
                "m3u8_secret": m3u8_secret,
                "vtt_secret": vtt_secret,
            })

    port, registry, _register, server = _build_proxy_server(
        initial, timeout, show_id=show_id, episode=episode,
        duration=duration, on_progress=on_progress
    )

    master_lines = [
        "#EXTM3U",
        "#EXT-X-VERSION:6",
    ]
    master_lines.extend(audio_lines)

    for sub in sub_entries:
        if sub["is_m3u8"]:
            sub_path = _register(sub["url"], referer, forwarded_headers)
            sub_m3u8_url = f"http://127.0.0.1:{port}{sub_path}"
        else:
            sub_m3u8_url = f"http://127.0.0.1:{port}{sub['m3u8_secret']}"
            sub_vtt_url = f"http://127.0.0.1:{port}{sub['vtt_secret']}"
            registry[sub["m3u8_secret"]]["text"] = (
                f"#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:1500\n"
                f"#EXT-X-MEDIA-SEQUENCE:1\n#EXT-X-PLAYLIST-TYPE:VOD\n#EXTINF:1500.0,\n"
                f"{sub_vtt_url}\n#EXT-X-ENDLIST\n"
            )

        master_lines.append(
            f'#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="subs",NAME="{sub["name"]}",'
            f'LANGUAGE="{sub["lang"]}",DEFAULT={sub["default"]},AUTOSELECT={sub["default"]},FORCED=NO,URI="{sub_m3u8_url}"'
        )

    audio_attr = ',AUDIO="audio"' if audio_lines else ""
    subs_attr = ',SUBTITLES="subs"' if sub_entries else ""
    codecs_attr = ',CODECS="mp4a.40.2,avc1.640028"'
    master_lines.append(
        f'#EXT-X-STREAM-INF:BANDWIDTH={int(bandwidth)},'
        f'RESOLUTION={int(width)}x{int(height)}{codecs_attr}{audio_attr}{subs_attr}'
    )
    master_lines.append("__VIDEO_URL__\n")
    master_text = "\n".join(master_lines)

    # Now that we know our own port, fill in the synthetic master with
    # local URLs pointing back at this same server.
    text = master_text.replace("__VIDEO_URL__", f"http://127.0.0.1:{port}{video_secret}")
    for a_secret, idx in audio_secrets:
        text = text.replace(f"__AUDIO_URL_{idx}__", f"http://127.0.0.1:{port}{a_secret}")
    registry[master_secret]["text"] = text

    return f"http://127.0.0.1:{port}{master_secret}", server


def start_local_content_server(content, filename, content_type):
    payload = (
        content.encode("utf-8")
        if isinstance(content, str)
        else bytes(content)
    )
    safe_name = re.sub(
        r"[^A-Za-z0-9._-]",
        "_",
        str(filename or "content"),
    )
    content_type = str(content_type or "application/octet-stream")
    if not re.fullmatch(
            r"[A-Za-z0-9.+-]+/[A-Za-z0-9.+-]+",
            content_type):
        content_type = "application/octet-stream"
    secret_path = f"/{uuid.uuid4().hex}/{safe_name}"

    class ContentHandler(http.server.BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def _serve(self, include_body):
            if not proxy_path_authorized(self.path, secret_path):
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if include_body:
                self.wfile.write(payload)

        def do_GET(self):
            self._serve(True)

        def do_HEAD(self):
            self._serve(False)

        def _reject_method(self):
            self.send_response(405)
            self.send_header("Allow", "GET, HEAD")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_OPTIONS(self):
            self.send_response(200)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "*")
            self.send_header("Content-Length", "0")
            self.end_headers()

        do_POST = _reject_method
        do_PUT = _reject_method
        do_DELETE = _reject_method
        do_PATCH = _reject_method

    server = _ThreadedHTTPServer(("127.0.0.1", 0), ContentHandler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{port}{secret_path}", server


def stop_local_proxy(server):
    if not server:
        return
    try:
        server.shutdown()
    except Exception as exc:
        _debug_warn("Local proxy shutdown failed", exc)
    try:
        server.server_close()
    except Exception as exc:
        _debug_warn("Local proxy close failed", exc)


def replace_active_local_proxy(server=None):
    global _active_server
    with _active_lock:
        previous = _active_server
        _active_server = server
    if previous is not None and previous is not server:
        stop_local_proxy(previous)
    return server


def cleanup_active_local_proxy():
    global _active_server
    with _active_lock:
        server = _active_server
        _active_server = None
    stop_local_proxy(server)
