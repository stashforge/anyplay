"""Persistent mpv process and IPC playback controller."""

import json
import logging
import math
import os
import random
import select
import socket
import subprocess
import sys
import termios
import threading
import time
import tty

from .mpv_runtime import (
    TRANSITION_OSD_MS,
    cleanup_mpv_runtime,
    create_mpv_runtime,
)
from .rules import (
    episode_transition_osd,
    next_up_osd,
    playback_is_actively_advancing,
    playback_failed_osd,
    prefetch_matches_request,
    skip_prompt_osd,
    skipped_interval_osd,
    startup_loading_status,
)

_logger = logging.getLogger(__name__)


class MpvStartupCancelled(Exception):
    """The caller retired startup before its IPC connection was ready."""


class MpvIpc:
    def __init__(self, redraw_callback=None):
        self.playback_menu = None
        self.menu_selection = None
        self.fullscreen_override = None
        self.audio_only = False
        self.repeat_control = None
        self.redraw_callback = redraw_callback
        self.runtime_dir = None
        self.socket_path = None
        self.conf_path = None
        self.process = None
        self.client = None
        self.running = False
        self.props = {
            "playback-time": 0,
            "duration": 0,
            "pause": False,
            "paused-for-cache": False,
            "cache-buffering-state": 0,
            "demuxer-cache-duration": None,
            "percent-pos": 0,
        }
        self.prefetched_ep = None
        self.prefetched_stream = None
        self.prefetched_res = None
        self.is_fetching = False
        self._pending_audio_url = ""
        self._pending_audio_tracks = []
        self._pending_subtitle_url = ""
        self._subtitle_languages = []
        self._remote_skip_lookup = None
        self._remote_skips_enabled = False
        self._active_entry_id = None
        self._replaced_entry_ids = set()
        self._ipc_buffer = ""
        self._played_in_process = False
        self._load_url = None
        self._load_serial = 0
        self._awaiting_start_file = False

    def _track_subtitle_preferences(self, msg):
        """Use mpv's effective configuration, including its own config profiles."""
        args = msg.get('args') or []
        if (msg.get('event') != 'client-message' or len(args) != 2
                or args[0] != 'anyplay_subtitle_preferences'):
            return
        try:
            languages = json.loads(args[1])
        except (TypeError, ValueError):
            return
        if isinstance(languages, list) and all(isinstance(lang, str) for lang in languages):
            self._subtitle_languages = [lang for lang in languages if lang.strip()]

    def _track_file_event(self, msg):
        """Identify end events for entries explicitly replaced by load()."""
        entry_id = msg.get("playlist_entry_id")
        if not isinstance(entry_id, int) or isinstance(entry_id, bool):
            return False
        if msg.get("event") == "start-file":
            self._active_entry_id = entry_id
        elif msg.get("event") == "end-file":
            replaced = entry_id in self._replaced_entry_ids
            self._replaced_entry_ids.discard(entry_id)
            if self._active_entry_id == entry_id:
                self._active_entry_id = None
            return replaced
        return False

    def _record_playback_end(self, event, *, reason=None, error=None, pending_action=None):
        def label(value, limit):
            text = str(value)[:limit] if value is not None else None
            return '[URL omitted]' if text and '://' in text else text

        try:
            returncode = self.process.poll() if self.process is not None else None
        except Exception:
            returncode = None
        _logger.debug(
            "mpv exit diagnostic: event=%s reason=%r error=%r process_returncode=%r "
            "pending_action=%r expected_transition=%s",
            label(event, 32), label(reason, 32), label(error, 160), returncode,
            label(pending_action, 32), bool(self._replaced_entry_ids),
        )

    def start(self, *, cancel_check=None):
        def check_cancelled():
            if cancel_check and cancel_check():
                raise MpvStartupCancelled

        check_cancelled()
        if self.process and self.process.poll() is None:
            if self.client:
                self.running = True
            return
        from ..services.requirements import require_executable

        require_executable("mpv", "Audio-only playback" if self.audio_only else "Desktop playback")
        self._played_in_process = False
        self._active_entry_id = None
        self._replaced_entry_ids.clear()
        self._ipc_buffer = ""
        cleanup_mpv_runtime(self.runtime_dir)
        self.runtime_dir, self.socket_path, self.conf_path, self.chapters_path, self.lua_path = create_mpv_runtime()
        from ..context import FLAGS
        window = "yes" if FLAGS.mpv_window == "immediate" else "no"
        if self.audio_only:
            window = "no"
        try:
            self.process = subprocess.Popen([
                "mpv", "--idle=yes", "--keep-open=no", f"--input-ipc-server={self.socket_path}",
                f"--input-conf={self.conf_path}", f"--chapters-file={self.chapters_path}",
                f"--script={self.lua_path}", f"--force-window={window}", "--no-ytdl", "--input-terminal=no",
                "--autoload-files=no", "--osd-shadow-offset=2", "--osd-shadow-color=#B0000000",
                # User visualizers can synthesize video despite vid=no.
                # Keep our explicit IPC script, but skip script autoload in audio mode.
                *(["--vid=no", "--audio-display=no", "--vo=null", "--load-scripts=no"]
                  if self.audio_only else []),
            ], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            for _ in range(20):
                check_cancelled()
                if os.path.exists(self.socket_path): break
                time.sleep(0.1)

            check_cancelled()
            self.client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.client.connect(self.socket_path)
            self.client.setblocking(False)
            self.running = True
            check_cancelled()
        except BaseException:
            self.quit()
            raise

        self.send_cmd("observe_property", 1, "playback-time")
        self.send_cmd("observe_property", 2, "duration")
        self.send_cmd("observe_property", 3, "pause")
        self.send_cmd("observe_property", 4, "percent-pos")
        self.send_cmd("observe_property", 5, "paused-for-cache")
        self.send_cmd("observe_property", 6, "cache-buffering-state")
        self.send_cmd("observe_property", 7, "demuxer-cache-duration")
        self.send_cmd("observe_property", 50, "fullscreen")
        for observer_id, name in enumerate(('aid', 'mute', 'volume', 'audio-codec', 'audio-params'), 8):
            self.send_cmd('observe_property', observer_id, name)

    def adopt_startup(self, prepared):
        """Move an accepted startup's process/socket into the persistent player."""
        if prepared is self or not prepared.running or prepared.process is None or prepared.client is None:
            raise ValueError("Startup has no ready mpv process and IPC connection")
        if self.process is not None and self.process.poll() is None:
            raise RuntimeError("Cannot replace an active mpv process")
        self.quit()
        for name in ("runtime_dir", "socket_path", "conf_path", "chapters_path", "lua_path", "process", "client"):
            setattr(self, name, getattr(prepared, name, None))
            setattr(prepared, name, None)
        self.running = True
        prepared.running = False

    def send_cmd(self, *args):
        if not self.running and not (self.process and self.process.poll() is None and self.client):
            return
        try:
            msg = json.dumps({"command": list(args)}) + "\n"
            self.client.sendall(msg.encode("utf-8"))
            self.running = True
        except Exception as exc:
            _logger.debug('mpv IPC send failed: exception=%s', type(exc).__name__)
            self.running = False

    def load(
            self, url, title, headers, referer, start_time=0, osd_msg="",
            audio_url="", subtitle_url="", subtitles=None, skip_intervals=None, aniskip_auto=True, audio_tracks=None,
            suppress_chapters=False, skip_lookup=None):
        self.start()
        from ..app.fullscreen_setting import apply_fullscreen

        if self.audio_only:
            self.send_cmd("set_property", "force-window", False)
            self.send_cmd("set_property", "vid", "no")
        else:
            apply_fullscreen(self)
        self._startup_osd_enabled = self._played_in_process
        self.running = True
        if self.client:
            try:
                while True:
                    while "\n" in self._ipc_buffer:
                        line, self._ipc_buffer = self._ipc_buffer.split("\n", 1)
                        try:
                            message = json.loads(line)
                            self._track_subtitle_preferences(message)
                            self._track_file_event(message)
                        except (ValueError, AttributeError):
                            pass
                    d = self.client.recv(8192)
                    if not d: break
                    self._ipc_buffer += d.decode("utf-8")
            except (OSError, BlockingIOError):
                pass
        self._awaiting_start_file = bool(self._active_entry_id is not None
                                         or self._replaced_entry_ids or self._awaiting_start_file)
        if self._active_entry_id is not None:
            self._replaced_entry_ids.add(self._active_entry_id)
            self._active_entry_id = None
        self.props["playback-time"] = 0
        self.props["duration"] = 0
        self.props["pause"] = False
        self.props["paused-for-cache"] = False
        self.props["cache-buffering-state"] = 0
        self.props["demuxer-cache-duration"] = None
        self.props["percent-pos"] = 0
        self.max_playback_time = float(start_time or 0.0)
        self.file_loaded = False
        self._remote_skips_enabled = skip_lookup is not None
        if self._remote_skip_lookup is not None:
            self._remote_skip_lookup.reset(None, None)
        if self._remote_skips_enabled:
            if self._remote_skip_lookup is None:
                from .skip_lookup import SkipLookup
                self._remote_skip_lookup = SkipLookup()
            self._remote_skip_lookup.reset(*skip_lookup, skip_intervals or ())
        self.skip_intervals = [] if self._remote_skips_enabled else (skip_intervals or [])
        self._suppress_skip_chapters = suppress_chapters
        self.aniskip_auto = aniskip_auto
        self.skipped_intervals = set()
        self.active_skip_prompt = None
        self.send_cmd("set_property", "force-media-title", title)
        headers_dict = dict(headers) if headers else {}
        ua = headers_dict.pop("User-Agent", None) or headers_dict.pop("user-agent", None)
        if ua:
            self.send_cmd("set_property", "user-agent", ua)
        ref = referer or headers_dict.pop("Referer", None) or headers_dict.pop("referer", None)
        if ref and "wixstatic" not in url:
            self.send_cmd("set_property", "referrer", ref)
        hf = [f"{k}: {v}" for k, v in headers_dict.items()]
        if hf:
            self.send_cmd("set_property", "http-header-fields", hf)

        # Override user's save-position-on-quit config to prevent cache hijacking
        self.send_cmd("set_property", "resume-playback", False)
        self.resume_time = start_time

        # If starting with an opening that begins at 0s, auto-seek resume_time to interval end
        skip_msg = ""
        if self.aniskip_auto and self.skip_intervals:
            first_skip = self.skip_intervals[0]
            if first_skip.get("end") is not None and first_skip["start"] <= 2.0 and (start_time == 0 or start_time < first_skip["start"]):
                self.resume_time = first_skip["end"]
                self.skipped_intervals.add(0)
                from ..ui.player_screen import _fmt_time
                skip_msg = skipped_interval_osd(
                    first_skip.get("label", "Opening"),
                    _fmt_time(first_skip["start"]),
                    _fmt_time(first_skip["end"]),
                )

        self._pending_audio_url = audio_url or ""
        self._pending_audio_tracks = list(audio_tracks) if audio_tracks else []
        self._pending_subtitle_url = subtitle_url or ""
        self._pending_subtitles = list(subtitles) if subtitles else []

        if getattr(self, "chapters_path", None):
            try:
                from ..media.aniskip import generate_chapters_file
                generate_chapters_file(self.skip_intervals, self.chapters_path)
            except Exception:
                pass

        self._load_started_mono = time.monotonic()
        self._load_serial = getattr(self, '_load_serial', 0) + 1
        self._load_url = url
        if getattr(self, "resume_time", 0) > 0:
            self.send_cmd("loadfile", url, "replace", -1, f"start={int(self.resume_time)}")
        else:
            self.send_cmd("loadfile", url, "replace")
        # Reset the global `start` property immediately so it doesn't bleed into
        # subsequent loadfile calls (e.g. next episode, mirror failover).
        self.send_cmd("set_property", "start", "none")
        self.send_cmd(
            "script-message",
            "set_skip_intervals",
            json.dumps(self.skip_intervals),
            "yes" if self.aniskip_auto else "no",
            "clear" if suppress_chapters else "keep",
        )

        msg_parts = []
        if skip_msg:
            msg_parts.append(skip_msg)
        msg_parts.append(f"Now playing\n{title}")
        msg_parts.append("Shift+Right: Next  •  Shift+Left: Previous  •  Q: Quit")
        if osd_msg:
            msg_parts.append(osd_msg)
        self.initial_osd_msg = "\n".join(msg_parts)

    def _refresh_remote_skips(self):
        if not self._remote_skips_enabled:
            return
        intervals = list(self._remote_skip_lookup.poll(self.props.get("duration", 0)))
        if intervals == self.skip_intervals:
            return
        self.skip_intervals = intervals
        self.skipped_intervals = set()
        self.active_skip_prompt = None
        self.send_cmd(
            "script-message", "set_skip_intervals", json.dumps(intervals),
            "yes" if self.aniskip_auto else "no",
            "clear" if self._suppress_skip_chapters or not intervals else "keep",
        )
        _logger.debug("Skip intervals activated: source=%s intervals=%r duration=%r",
                      "provider" if any(row.get("source") == "provider" for row in intervals) else "aniskip",
                      [(row.get("type"), row.get("start"), row.get("end")) for row in intervals],
                      self.props.get("duration"))

    def queue_subtitles(self, tracks, *, load_serial):
        """Called only by the playback owner; reject a retired load's result."""
        if load_serial != self._load_serial:
            return
        self._pending_subtitles = list(tracks)
        self._pending_subtitle_url = next(
            (s.get('url') or s.get('file') for s in tracks if s.get('default')),
            (tracks[0].get('url') or tracks[0].get('file') or '') if tracks else '',
        )
        if getattr(self, 'file_loaded', False) and not self._awaiting_start_file:
            self._attach_pending_external_tracks()

    def _attach_pending_external_tracks(self):
        if not any((getattr(self, '_pending_audio_tracks', None), self._pending_audio_url,
                    getattr(self, '_pending_subtitles', None), self._pending_subtitle_url)):
            return
        _logger.debug('mpv external tracks: entry=%r audio_tracks=%d audio_url=%s subtitles=%d subtitle_url=%s',
                      self._active_entry_id, len(getattr(self, '_pending_audio_tracks', ()) or ()),
                      bool(self._pending_audio_url), len(getattr(self, '_pending_subtitles', ()) or ()),
                      bool(self._pending_subtitle_url))
        if getattr(self, "_pending_audio_tracks", None):
            has_selected = False
            for track in self._pending_audio_tracks:
                a_url = track.get("url")
                a_label = track.get("label") or "Audio"
                a_lang = track.get("language") or ""
                if a_url:
                    is_def = bool(track.get("default")) or not has_selected
                    mode = "select" if (is_def and not has_selected) else "auto"
                    if mode == "select":
                        has_selected = True
                    if a_lang:
                        self.send_cmd("audio-add", a_url, mode, a_label, a_lang)
                    else:
                        self.send_cmd("audio-add", a_url, mode, a_label)
            self._pending_audio_tracks = []
            self._pending_audio_url = ""
        elif self._pending_audio_url:
            self.send_cmd("audio-add", self._pending_audio_url, "select")
            self._pending_audio_url = ""
        if getattr(self, "_pending_subtitles", None):
            from ..media.languages import match_track_language, normalize_lang_code
            preferred = self._subtitle_languages
            selected = None
            if preferred:
                selected = next((index for lang in preferred
                                 for index, sub in enumerate(self._pending_subtitles)
                                 if (sub.get('url') or sub.get('file')) and match_track_language(
                                     sub, {normalize_lang_code(lang)})), None)
            for index, sub in enumerate(self._pending_subtitles):
                sub_file = sub.get("url") or sub.get("file")
                sub_label = sub.get("label") or "Subtitle"
                if sub_file:
                    is_def = bool(sub.get("default") or sub_file == self._pending_subtitle_url)
                    mode = "select" if (index == selected if preferred else is_def) else "auto"
                    language = sub.get('language') or sub.get('lang') or ''
                    if not language and preferred and index == selected:
                        language = normalize_lang_code(sub_label, target='alpha3')
                    if language:
                        self.send_cmd("sub-add", sub_file, mode, sub_label, language)
                    else:
                        self.send_cmd("sub-add", sub_file, mode, sub_label)
            self._pending_subtitles = []
            self._pending_subtitle_url = ""
        elif self._pending_subtitle_url:
            self.send_cmd("sub-add", self._pending_subtitle_url,
                          "auto" if self._subtitle_languages else "select")
            self._pending_subtitle_url = ""


    def song_key(self, key):
        """Player-screen controls scoped to the active song collection."""
        if self.repeat_control is None:
            return None
        if key.lower() == "r":
            label = self.repeat_control()
            self.send_cmd("show-text", f"Repeat: {label}", 2000)
            return "HANDLED"
        if self.audio_only:
            if key in ("LEFT", "RIGHT"):
                self.send_cmd("seek", -5 if key == "LEFT" else 5, "relative")
                return "HANDLED"
            if key == " ":
                self.send_cmd("cycle", "pause")
                return "HANDLED"
        if key in ("SHIFT_LEFT", "SHIFT_RIGHT"):
            return "PREV" if key == "SHIFT_LEFT" else "NEXT"
        return None

    def quit(self):
        if self._remote_skip_lookup is not None:
            self._remote_skip_lookup.close()
            self._remote_skip_lookup = None
        self._remote_skips_enabled = False
        self.send_cmd("quit")
        if self.client:
            try: self.client.close()
            except Exception: pass
            self.client = None
        if self.process:
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                try:
                    self.process.terminate()
                    self.process.wait(timeout=2)
                except Exception:
                    try:
                        self.process.kill()
                    except Exception:
                        pass
            self.process = None
        self.running = False
        self._played_in_process = False
        self._active_entry_id = None
        self._replaced_entry_ids.clear()
        self._ipc_buffer = ""
        self._load_url = None
        self._awaiting_start_file = False
        cleanup_mpv_runtime(self.runtime_dir)
        self.runtime_dir = None
        self.socket_path = None
        self.conf_path = None

    def wait_for_playback(self, ui_info, current_ep, total_eps, fetch_callback, is_binge=False, *, terminal_managed=False, poll_subtitles=None):
        tty_fd = sys.stdin.fileno()
        if not terminal_managed and os.isatty(tty_fd):
            old_attrs = termios.tcgetattr(tty_fd)
            tty.setcbreak(tty_fd)
        else:
            old_attrs = None

        result = "QUIT"
        buf, self._ipc_buffer = self._ipc_buffer, ""
        want_skip_to = None
        want_skip_ep = None
        notify_prefetched = None
        countdown_active = False
        initial_osd_shown = False
        played_seconds = 0.0
        last_playback_tick = time.monotonic()
        playback_start_mono = time.monotonic()
        last_time_pos_change = playback_start_mono
        file_loaded = False
        last_observed_playback_time = -1.0
        max_playback_time = float(getattr(self, "resume_time", 0) or 0.0)
        self.props["_startup_elapsed"] = 0.0
        self.props['_mirror_wait_message'] = None
        prompt_active = False
        prompt_second = None
        prompt_serial = str(self._load_serial)
        wait_message = random.choice((
            'Still loading… Press Enter to try the next mirror.',
            'Taking a little longer… Press Enter to switch mirrors.',
            'Still waiting for playback… Press Enter to try another mirror.',
        ))
        timing_started = getattr(self, "_load_started_mono", None)
        if timing_started is None:
            timing_started = playback_start_mono
        timing_stages = set()
        last_startup_osd_second = None
        event_counts = {}
        retired_notifications = set()
        handler_errors = set()
        probe_requests = {}
        probe_states = {}
        next_probe_at = 20.0
        probe_number = 0
        next_state_log_at = 10.0

        def clear_mirror_prompt():
            nonlocal prompt_active, prompt_second
            if prompt_active:
                self.send_cmd('script-message', 'set_mirror_prompt', prompt_serial, 'no')
                self.send_cmd('show-text', '')
            prompt_active = False
            prompt_second = None
            self.props['_mirror_wait_message'] = None

        def clear_startup_osd():
            nonlocal last_startup_osd_second
            clear_mirror_prompt()
            if last_startup_osd_second is not None:
                self.send_cmd("show-text", "")
                last_startup_osd_second = None

        def record_startup_timing(stage, event):
            if stage in timing_stages:
                return
            timing_stages.add(stage)
            if stage == "ready":
                if not self._played_in_process and not self.audio_only:
                    # Videos create their normal output window when initialized;
                    # audio-only media gets its player window once ready as well.
                    self.send_cmd("set_property", "force-window", True)
                self._played_in_process = True

            def label(value, limit):
                text = str(value or "")
                return "[URL omitted]" if "://" in text else text[:limit]

            _logger.debug(
                "mpv startup timing: stage=%s elapsed=%.3fs mirror=%r quality=%r event=%s",
                stage, max(0.0, time.monotonic() - timing_started),
                label(ui_info.get("mirror"), 120), label(ui_info.get("quality"), 64), event,
            )

        def do_fetch(ep_target, action):
            nonlocal notify_prefetched
            try:
                res = fetch_callback(ep_target)
                if self.prefetched_ep == ep_target and res:
                    self.prefetched_stream = res[0]
                    self.prefetched_res = res
            except Exception: pass
            self.is_fetching = False
            notify_prefetched = action

        def open_playback_menu(kind):
            nonlocal buf
            if self.playback_menu is None:
                return None
            self._ipc_buffer = buf + self._ipc_buffer
            buf = ""
            outcome, selection = self.playback_menu(kind)
            buf += self._ipc_buffer
            self._ipc_buffer = ""
            if outcome == "SELECT":
                self.menu_selection = selection
                return "MENU_SELECTION"
            if outcome == "ENDED":
                return "MENU_ENDED" if self.props.pop("_menu_end_reason", "quit") == "eof" else "QUIT"
            return None

        def trigger_fetch(ep_target, action="NEXT"):
            if self.is_fetching: return
            if self.prefetched_ep == ep_target and self.prefetched_stream: return
            if not fetch_callback: return
            self.prefetched_ep = ep_target
            self.prefetched_stream = None
            self.prefetched_res = None
            self.is_fetching = True
            threading.Thread(
                target=do_fetch,
                args=(ep_target, action),
                daemon=True,
            ).start()

        current_ord = int(ui_info.get("episode_index", 0)) + 1
        next_ord = current_ord + 1
        prev_ord = current_ord - 1
        next_label = ui_info.get("next_episode") or next_ord

        def fmt_time(sec):
            if not sec: return "00:00"
            m, s = divmod(int(sec), 60)
            if m >= 60:
                h, m = divmod(m, 60)
                return f"{h:02d}:{m:02d}:{s:02d}"
            return f"{m:02d}:{s:02d}"

        def redraw():
            if self.redraw_callback:
                try:
                    self.redraw_callback(self.props)
                except Exception as exc:
                    error = ('redraw', type(exc).__name__)
                    if error not in handler_errors:
                        handler_errors.add(error)
                        _logger.debug('mpv redraw failed: exception=%s', type(exc).__name__)

        def process_exited():
            try:
                return self.process is not None and self.process.poll() is not None
            except Exception:
                return False

        try:
            done = False
            pending_action = None
            while self.running and not done:
                if poll_subtitles is not None:
                    poll_subtitles()
                now = time.monotonic()
                elapsed = max(0.0, now - playback_start_mono)
                if not file_loaded and elapsed >= next_state_log_at:
                    next_state_log_at += 10.0
                    _logger.debug(
                        'mpv startup state: elapsed=%.2f position=%r duration=%r pause=%r cache_pause=%r '
                        'buffering=%r events=%r handler_errors=%d buffered_bytes=%d',
                        elapsed, self.props.get('playback-time'), self.props.get('duration'),
                        self.props.get('pause'), self.props.get('paused-for-cache'),
                        self.props.get('cache-buffering-state'), event_counts, len(handler_errors), len(buf),
                    )
                if not file_loaded and elapsed >= next_probe_at:
                    next_probe_at += 5.0
                    # Waiting is unlimited; retain only the latest probe pair.
                    probe_number += 1
                    probe_states.clear()
                    probe_requests.clear()
                    probe_states[probe_number] = {}
                    for property_name in ('path', 'time-pos'):
                        request_id = f"startup:{getattr(self, '_load_serial', 0)}:{probe_number}:{property_name}"
                        try:
                            self.client.sendall((json.dumps({
                                'command': ['get_property', property_name], 'request_id': request_id,
                            }) + '\n').encode())
                            probe_requests[request_id] = (probe_number, property_name)
                            _logger.debug('mpv readiness query sent: property=%s elapsed=%.2f', property_name, elapsed)
                        except Exception as exc:
                            _logger.debug('mpv readiness query send failed: exception=%s', type(exc).__name__)
                self.props["_startup_elapsed"] = None if file_loaded else max(0.0, now - playback_start_mono)
                if not file_loaded and not prompt_active and getattr(self, "_startup_osd_enabled", False):
                    startup_elapsed = self.props["_startup_elapsed"]
                    if int(startup_elapsed) != last_startup_osd_second:
                        last_startup_osd_second = int(startup_elapsed)
                        mirror = str(ui_info.get("mirror") or "mirror").replace("\n", " ").replace("\r", " ")[:120]
                        self.send_cmd(
                            "show-text", f"Loading {mirror}\n{startup_loading_status(startup_elapsed, audio_only=self.audio_only)}", 1500,
                        )
                elapsed = max(0.0, now - last_playback_tick)
                last_playback_tick = now
                self._refresh_remote_skips()
                if playback_is_actively_advancing(
                    self.props, initial_osd_shown
                ):
                    played_seconds += min(elapsed, 1.0)
                r, _, _ = select.select([self.client, sys.stdin], [], [], 0.2)

                if notify_prefetched:
                    completed_action = notify_prefetched
                    notify_prefetched = False
                    if want_skip_to == completed_action:
                        if (
                            prefetch_matches_request(
                                self.prefetched_ep, want_skip_ep
                            )
                            and self.prefetched_stream
                        ):
                            self.send_cmd(
                                "show-text",
                                episode_transition_osd(
                                    completed_action, "starting"
                                ),
                                TRANSITION_OSD_MS,
                            )
                        elif prefetch_matches_request(
                            self.prefetched_ep, want_skip_ep
                        ):
                            self.send_cmd(
                                "show-text",
                                episode_transition_osd(
                                    completed_action, "failed"
                                ),
                                5000,
                            )
                            want_skip_to = None
                            want_skip_ep = None
                    elif want_skip_to:
                        pass
                    elif self.prefetched_stream:
                        self.send_cmd(
                            "show-text",
                            episode_transition_osd(completed_action, "ready"),
                            3000,
                        )
                    else:
                        self.send_cmd(
                            "show-text",
                            episode_transition_osd(completed_action, "failed"),
                            5000,
                        )

                # Check if we have a delayed skip that is now ready
                if want_skip_to and not self.is_fetching:
                    if not prefetch_matches_request(
                        self.prefetched_ep, want_skip_ep
                    ):
                        self.send_cmd(
                            "show-text",
                            episode_transition_osd(want_skip_to, "loading"),
                            TRANSITION_OSD_MS,
                        )
                        trigger_fetch(want_skip_ep, want_skip_to)
                    elif self.prefetched_stream:
                        self.send_cmd(
                            "show-text",
                            episode_transition_osd(want_skip_to, "starting"),
                            TRANSITION_OSD_MS,
                        )
                        result = want_skip_to
                        want_skip_to = None
                        want_skip_ep = None
                        done = True
                        break
                    else:
                        failed_action = want_skip_to
                        want_skip_to = None
                        want_skip_ep = None
                        self.send_cmd(
                            "show-text",
                            episode_transition_osd(failed_action, "failed"),
                            5000,
                        )

                if sys.stdin in r:
                    if self.repeat_control is not None:
                        from ..ui.picker_render import get_key
                        key = get_key(tty_fd)
                    else:
                        key = sys.stdin.read(1)
                    song_action = self.song_key(key)
                    if song_action == "HANDLED":
                        redraw()
                        key = ""
                    if song_action in ("NEXT", "PREV"):
                        index = ui_info.get("episode_index", 0)
                        if ((song_action == "NEXT" and index + 1 < total_eps)
                                or (song_action == "PREV" and index > 0)):
                            result = song_action
                            self.send_cmd("stop")
                            break
                        continue
                    key = {"CTRL_C": "\x03", "ENTER": "\r", "TAB": "\t"}.get(key, key)
                    if key == "\x03":
                        raise KeyboardInterrupt
                    if key.lower() == 'q':
                        pending_action = "QUIT"
                        self.send_cmd("stop")
                    elif key.lower() in ("m", "v") and self.playback_menu is not None:
                        menu_result = open_playback_menu(key.lower())
                        if menu_result:
                            result = menu_result
                            break
                    elif key in ('\r', '\n') and prompt_active:
                        result = 'NEXT_MIRROR'
                        self.send_cmd('stop')
                        break
                    elif key == '\t' and not self.aniskip_auto and self.skip_intervals:
                        curr_time = self.props.get("playback-time", 0) or 0
                        for s_idx, s_item in enumerate(self.skip_intervals):
                            s_start = s_item["start"]
                            s_end = s_item.get("end")
                            if s_end is None:
                                s_end = self.props.get("duration") or 0
                            if not s_end or s_end <= s_start:
                                continue
                            s_label = s_item.get("label", "Opening")
                            if s_start <= curr_time < s_end:
                                self.skipped_intervals.add(s_idx)
                                self.send_cmd("seek", s_end, "absolute")
                                self.send_cmd(
                                    "show-text",
                                    skipped_interval_osd(s_label, fmt_time(s_start), fmt_time(s_end)),
                                    3000,
                                )
                                self.active_skip_prompt = None
                                break
                if self.client in r:
                    try:
                        data = self.client.recv(4096)
                        if not data:
                            self._record_playback_end('ipc-disconnect', pending_action=pending_action)
                            if pending_action:
                                result = pending_action
                            elif not file_loaded:
                                result = "ERROR"
                            else:
                                result = "QUIT"
                            self.running = False; break
                        buf += data.decode("utf-8")
                        while "\n" in buf:
                            line, buf = buf.split("\n", 1)
                            if not line: continue
                            try:
                                msg = json.loads(line)
                                self._track_subtitle_preferences(msg)
                                ev = msg.get("event")
                                if ev in ('start-file', 'file-loaded', 'playback-restart', 'seek', 'end-file', 'property-change'):
                                    event_counts[ev] = event_counts.get(ev, 0) + 1
                                    if ev != 'property-change':
                                        _logger.debug('mpv lifecycle: event=%s elapsed=%.2f entry=%r',
                                                      ev, now - playback_start_mono, self._active_entry_id)
                                query = probe_requests.pop(msg.get('request_id'), None)
                                if query is not None:
                                    probe_number, property_name = query
                                    success = msg.get('error') == 'success'
                                    state = probe_states[probe_number]
                                    if success:
                                        state[property_name] = msg.get('data')
                                    position = state.get('time-pos')
                                    current_file = isinstance(state.get('path'), str) and state['path'] == getattr(self, '_load_url', None)
                                    _logger.debug('mpv readiness query reply: property=%s success=%s current_file=%s',
                                                  property_name, success, current_file)
                                    if (not file_loaded and not self._awaiting_start_file and current_file and isinstance(position, (int, float))
                                            and not isinstance(position, bool) and math.isfinite(position) and position > 0):
                                        file_loaded = self.file_loaded = True
                                        self.props['playback-time'] = position
                                        self.props['_startup_elapsed'] = None
                                        max_playback_time = max(max_playback_time, position)
                                        last_observed_playback_time = position
                                        last_time_pos_change = now
                                        clear_startup_osd()
                                        record_startup_timing('ready', 'state-query')
                                        self._attach_pending_external_tracks()
                                if ev == "start-file":
                                    if msg.get('playlist_entry_id') in self._replaced_entry_ids:
                                        _logger.debug('Ignoring retired mpv notification: event=start-file')
                                        continue
                                    self._track_file_event(msg)
                                    if self._awaiting_start_file and self._active_entry_id is not None:
                                        _logger.debug('mpv replacement started: entry=%r queued=%.3fs',
                                                      self._active_entry_id, now - playback_start_mono)
                                        self._awaiting_start_file = False
                                        file_loaded = self.file_loaded = False
                                        playback_start_mono = now
                                        last_time_pos_change = last_playback_tick = now
                                        next_probe_at, next_state_log_at = 20.0, 10.0
                                        probe_requests.clear()
                                        self.props['_startup_elapsed'] = 0.0
                                if (self._awaiting_start_file
                                        and ev in ('file-loaded', 'playback-restart', 'seek', 'property-change')):
                                    if ev not in retired_notifications:
                                        retired_notifications.add(ev)
                                        _logger.debug('Ignoring retired mpv notification: event=%s', ev)
                                    continue
                                if ev == "file-loaded":
                                    clear_startup_osd()
                                    record_startup_timing("loaded", ev)
                                    file_loaded = True
                                    self.file_loaded = True
                                    self.props["_startup_elapsed"] = None
                                    self._attach_pending_external_tracks()
                                    if self.skip_intervals:
                                        self.send_cmd(
                                            "script-message",
                                            "set_skip_intervals",
                                            json.dumps(self.skip_intervals),
                                            "yes" if self.aniskip_auto else "no",
                                        )
                                elif ev in ("playback-restart", "seek"):
                                    clear_startup_osd()
                                    if ev == "playback-restart":
                                        record_startup_timing("ready", ev)
                                        self._attach_pending_external_tracks()
                                    file_loaded = True
                                    self.file_loaded = True
                                    self.props["_startup_elapsed"] = None
                                    last_time_pos_change = now
                                elif ev == "end-file":
                                    self._record_playback_end(
                                        'end-file', reason=msg.get('reason'), error=msg.get('error'),
                                        pending_action=pending_action,
                                    )
                                    replaced_entry = self._track_file_event(msg)
                                    if replaced_entry and not pending_action:
                                        _logger.debug(
                                            "Ignoring replaced mpv entry end: playlist_entry_id=%s",
                                            msg.get("playlist_entry_id"),
                                        )
                                        continue

                                    reason = msg.get("reason")
                                    dur = float(self.props.get("duration", 0) or 0)
                                    curr_pos = max_playback_time

                                    if pending_action:
                                        result = pending_action
                                        if pending_action == "QUIT":
                                            self.running = False
                                    elif reason == "error":
                                        if dur > 0 and curr_pos >= (dur - 30.0):
                                            result = "EOF"
                                        else:
                                            result = "ERROR"
                                            _logger.warning(
                                                "mpv playback error fallback reason=%r file_loaded=%s duration=%.2f current=%.2f played=%.2f",
                                                reason,
                                                file_loaded,
                                                dur,
                                                curr_pos,
                                                played_seconds,
                                            )
                                            self.send_cmd("show-text", playback_failed_osd("failed"), 10000)
                                        if process_exited():
                                            self.running = False
                                    elif reason in ("quit", "stop"):
                                        result = "QUIT"
                                        self.running = False
                                    elif reason == "eof":
                                        if dur > 60.0 and curr_pos < (dur * 0.90) and curr_pos < (dur - 60.0):
                                            result = "ERROR"
                                            _logger.warning(
                                                "mpv premature eof fallback reason=%r file_loaded=%s duration=%.2f current=%.2f played=%.2f",
                                                reason,
                                                file_loaded,
                                                dur,
                                                curr_pos,
                                                played_seconds,
                                            )
                                            self.send_cmd("show-text", playback_failed_osd("ended"), 10000)
                                        elif not file_loaded or (played_seconds < 2.0 and curr_pos <= 0.0):
                                            result = "ERROR"
                                            _logger.warning(
                                                "mpv early eof fallback reason=%r file_loaded=%s duration=%.2f current=%.2f played=%.2f",
                                                reason,
                                                file_loaded,
                                                dur,
                                                curr_pos,
                                                played_seconds,
                                            )
                                        else:
                                            result = "EOF"
                                    done = True
                                    break
                                elif ev == "property-change":
                                    name = msg.get("name")
                                    val = msg.get("data")
                                    if name in ('aid', 'mute', 'volume', 'audio-codec', 'audio-params'):
                                        if name == 'audio-params':
                                            params = val if isinstance(val, dict) else {}
                                            _logger.debug('mpv audio state: entry=%r available=%s channels=%r samplerate=%r',
                                                          self._active_entry_id, bool(params),
                                                          params.get('channel-count'), params.get('samplerate'))
                                        else:
                                            value = val if isinstance(val, (bool, int, float)) else bool(val)
                                            _logger.debug('mpv audio state: entry=%r property=%s value=%r',
                                                          self._active_entry_id, name, value)
                                    if name in self.props:
                                        if val is not None:
                                            self.props[name] = val
                                        if name == "duration":
                                            self._refresh_remote_skips()
                                        redraw()

                                        if name == "playback-time" and val is not None:
                                            curr_val = float(val or 0)
                                            if curr_val > 0.0:
                                                record_startup_timing("ready", "first-position")
                                                self._attach_pending_external_tracks()
                                                file_loaded = True
                                                self.file_loaded = True
                                                self.props["_startup_elapsed"] = None
                                            if last_observed_playback_time < 0 or abs(curr_val - last_observed_playback_time) >= 0.01:
                                                last_time_pos_change = now
                                                last_observed_playback_time = curr_val
                                            if curr_val > max_playback_time:
                                                max_playback_time = curr_val
                                        elif name == "pause":
                                            last_time_pos_change = now
                                        elif name in ("duration", "percent-pos") and float(val or 0) > 0:
                                            file_loaded = True
                                            self.file_loaded = True

                                        if file_loaded:
                                            clear_startup_osd()
                                        if name == "playback-time" and val is not None and not initial_osd_shown:
                                            initial_osd_shown = True
                                            r_time = getattr(self, "resume_time", 0) or 0
                                            curr_val = float(val or 0)
                                            # Only seek if MPV failed to start at resume_time (e.g. started at 00:00 instead)
                                            if r_time > 0 and abs(curr_val - r_time) > 5.0 and curr_val < r_time:
                                                self.send_cmd("seek", r_time, "absolute")
                                            if getattr(self, "initial_osd_msg", None):
                                                self.send_cmd("show-text", self.initial_osd_msg, 5000)

                                        if name == "playback-time" and val is not None and self.skip_intervals:
                                            curr_time = float(val)
                                            for s_idx, s_item in enumerate(self.skip_intervals):
                                                s_start = s_item["start"]
                                                s_end = s_item.get("end")
                                                if s_end is None:
                                                    s_end = self.props.get("duration") or 0
                                                if not s_end or s_end <= s_start:
                                                    continue
                                                s_label = s_item.get("label", "Opening")
                                                if s_start <= curr_time < (s_end - 0.5):
                                                    if s_idx not in self.skipped_intervals:
                                                        if self.aniskip_auto:
                                                            self.skipped_intervals.add(s_idx)
                                                            self._last_seek_mono = time.monotonic()
                                                            self.send_cmd("seek", s_end, "absolute")
                                                            self.send_cmd(
                                                                "show-text",
                                                                skipped_interval_osd(s_label, fmt_time(s_start), fmt_time(s_end)),
                                                                3000,
                                                            )
                                                            self.active_skip_prompt = None
                                                        else:
                                                            if self.active_skip_prompt != s_idx:
                                                                self.active_skip_prompt = s_idx
                                                                self.send_cmd(
                                                                    "show-text",
                                                                    skip_prompt_osd(s_label, fmt_time(s_start), fmt_time(s_end)),
                                                                    int(max(1.0, (s_end - curr_time)) * 1000),
                                                                )
                                                elif curr_time >= (s_end - 0.5):
                                                    self.skipped_intervals.add(s_idx)
                                                    if self.active_skip_prompt == s_idx:
                                                        self.active_skip_prompt = None
                                                elif curr_time < s_start:
                                                    if self.active_skip_prompt == s_idx:
                                                        self.active_skip_prompt = None

                                        if name == "playback-time" and current_ord < total_eps and is_binge:
                                            pt = self.props.get("playback-time")
                                            dur = self.props.get("duration")
                                            if pt and dur and dur > 0:
                                                rem_sec = dur - pt
                                                ratio = pt / dur
                                                if rem_sec <= 150 and ratio >= 0.75 and not self.is_fetching and not self.prefetched_stream:
                                                    self.send_cmd("show-text", "Getting next episode ready", 3000)
                                                    trigger_fetch(next_ord, "NEXT")

                                                if self.prefetched_stream:
                                                    from media_cli.domain.episodes import (
                                                        clean_episode_identifier,
                                                        episode_label,
                                                    )
                                                    raw_next = str(episode_label(next_label))
                                                    ep_str = clean_episode_identifier(raw_next) or raw_next
                                                    if ep_str and ep_str[0].isdigit():
                                                        ep_str = f"EP {ep_str}"
                                                    ntitle = f"{ui_info.get('title', 'Anime')} - {ep_str}"
                                                    if rem_sec <= 30 and rem_sec > 5:
                                                        self.send_cmd("show-text", next_up_osd(ntitle, rem_sec), 60000)
                                                        countdown_active = True
                                                    elif rem_sec <= 5:
                                                        self.send_cmd("show-text", "Starting next episode", 60000)
                                                        countdown_active = True
                                                    elif countdown_active:
                                                        self.send_cmd("show-text", "")
                                                        countdown_active = False

                                                if rem_sec <= 5 and self.prefetched_stream:
                                                    result = "NEXT"
                                                    done = True
                                                    break

                                elif ev == "client-message":
                                    args = msg.get("args", [])
                                    if args:
                                        if args[0] in ("playback_mirrors", "playback_versions") and self.playback_menu is not None:
                                            menu_result = open_playback_menu("m" if args[0] == "playback_mirrors" else "v")
                                            if menu_result:
                                                result = menu_result
                                                done = True
                                                break
                                        elif (args[0] == 'next_mirror' and prompt_active
                                                and len(args) > 1 and args[1] == prompt_serial):
                                            result = 'NEXT_MIRROR'
                                            self.send_cmd('stop')
                                            done = True
                                            break
                                        elif args[0] in ("skip_interval", "skip_op", "skip_ed") and self.skip_intervals:
                                            curr_time = self.props.get("playback-time", 0) or 0
                                            for s_idx, s_item in enumerate(self.skip_intervals):
                                                s_start = s_item["start"]
                                                s_end = s_item.get("end")
                                                if s_end is None:
                                                    s_end = self.props.get("duration") or 0
                                                if not s_end or s_end <= s_start:
                                                    continue
                                                s_label = s_item.get("label", "Opening")
                                                if s_start <= curr_time < s_end:
                                                    self.skipped_intervals.add(s_idx)
                                                    self.send_cmd("seek", s_end, "absolute")
                                                    self.send_cmd(
                                                        "show-text",
                                                        skipped_interval_osd(s_label, fmt_time(s_start), fmt_time(s_end)),
                                                        3000,
                                                    )
                                                    self.active_skip_prompt = None
                                                    break
                                        elif args[0] == "next_ep":
                                            if current_ord >= total_eps:
                                                self.send_cmd("show-text", "This is the last episode", 3000)
                                            else:
                                                want_skip_to = "NEXT"
                                                want_skip_ep = next_ord
                                                if self.prefetched_ep != next_ord or not self.prefetched_stream:
                                                    self.send_cmd(
                                                        "show-text",
                                                        episode_transition_osd(
                                                            "NEXT", "loading"
                                                        ),
                                                        TRANSITION_OSD_MS,
                                                    )
                                                    trigger_fetch(next_ord, "NEXT")
                                                else:
                                                    self.send_cmd(
                                                        "show-text",
                                                        episode_transition_osd(
                                                            "NEXT", "starting"
                                                        ),
                                                        TRANSITION_OSD_MS,
                                                    )
                                        elif args[0] == "prev_ep":
                                            if current_ord <= 1:
                                                self.send_cmd("show-text", "This is the first episode", 3000)
                                            else:
                                                want_skip_to = "PREV"
                                                want_skip_ep = prev_ord
                                                if self.prefetched_ep != prev_ord or not self.prefetched_stream:
                                                    self.send_cmd(
                                                        "show-text",
                                                        episode_transition_osd(
                                                            "PREV", "loading"
                                                        ),
                                                        TRANSITION_OSD_MS,
                                                    )
                                                    trigger_fetch(prev_ord, "PREV")
                                                else:
                                                    self.send_cmd(
                                                        "show-text",
                                                        episode_transition_osd(
                                                            "PREV", "starting"
                                                        ),
                                                        TRANSITION_OSD_MS,
                                                    )
                            except Exception as exc:
                                error = ('ipc', type(exc).__name__)
                                if error not in handler_errors:
                                    handler_errors.add(error)
                                    _logger.debug('mpv IPC handler failed: exception=%s', type(exc).__name__)
                    except BlockingIOError: pass

                # Silence is not proof of failure. Offer a choice, never expire
                # a slow startup/buffering attempt or count a user pause as a stall.
                if self.props.get('pause'):
                    last_time_pos_change = now
                if not done and not self.props.get('pause') and now - last_time_pos_change >= 60:
                    if not prompt_active:
                        prompt_active = True
                        self.props['_mirror_wait_message'] = wait_message
                        self.send_cmd('script-message', 'set_mirror_prompt', prompt_serial, 'yes')
                        _logger.debug('mpv slow mirror choice: entry=%r idle=%.2fs',
                                      self._active_entry_id, now - last_time_pos_change)
                    if prompt_second != int(now):
                        prompt_second = int(now)
                        self.send_cmd('show-text', wait_message, 1500)
                else:
                    clear_mirror_prompt()

                redraw()
        finally:
            clear_mirror_prompt()
            # Idle mode is useful for episode/mirror transitions, but must not
            # leave an empty window up while the caller saves completion state.
            if result in ("QUIT", "MENU_ENDED") or (result == "EOF" and not is_binge):
                self.send_cmd("quit")
            if "ready" not in timing_stages:
                stage = "failed" if result == "ERROR" else "cancelled" if result == "QUIT" else "ended-before-ready"
                record_startup_timing(stage, result)
            self._ipc_buffer = buf
            if old_attrs: termios.tcsetattr(tty_fd, termios.TCSADRAIN, old_attrs)
            print()

        if max_playback_time > (float(self.props.get("playback-time") or 0)):
            self.props["playback-time"] = max_playback_time

        return result, played_seconds
