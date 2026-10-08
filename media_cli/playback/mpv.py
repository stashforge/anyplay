"""Persistent mpv process and IPC playback controller."""

import json
import logging
import os
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
    playback_is_actively_advancing,
    prefetch_matches_request,
)

_logger = logging.getLogger(__name__)


class MpvIpc:
    def __init__(self, redraw_callback=None):
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
            "percent-pos": 0,
        }
        self.prefetched_ep = None
        self.prefetched_stream = None
        self.prefetched_res = None
        self.is_fetching = False
        self._pending_audio_url = ""
        self._pending_audio_tracks = []
        self._pending_subtitle_url = ""

    def start(self):
        if self.process and self.process.poll() is None:
            if self.client:
                self.running = True
            return
        cleanup_mpv_runtime(self.runtime_dir)
        self.runtime_dir, self.socket_path, self.conf_path, self.chapters_path, self.lua_path = create_mpv_runtime()
        try:
            self.process = subprocess.Popen([
                "mpv", "--idle=yes", "--keep-open=no", f"--input-ipc-server={self.socket_path}",
                f"--input-conf={self.conf_path}", f"--chapters-file={self.chapters_path}",
                f"--script={self.lua_path}", "--force-window=yes", "--no-ytdl",
                "--autoload-files=no",
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            for _ in range(20):
                if os.path.exists(self.socket_path): break
                time.sleep(0.1)

            self.client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.client.connect(self.socket_path)
            self.client.setblocking(False)
            self.running = True
        except Exception:
            self.quit()
            raise

        self.send_cmd("observe_property", 1, "playback-time")
        self.send_cmd("observe_property", 2, "duration")
        self.send_cmd("observe_property", 3, "pause")
        self.send_cmd("observe_property", 4, "percent-pos")
        self.send_cmd("observe_property", 5, "paused-for-cache")

    def send_cmd(self, *args):
        if not self.running and not (self.process and self.process.poll() is None and self.client):
            return
        try:
            msg = json.dumps({"command": list(args)}) + "\n"
            self.client.sendall(msg.encode("utf-8"))
            self.running = True
        except Exception:
            self.running = False

    def load(
            self, url, title, headers, referer, start_time=0, osd_msg="",
            audio_url="", subtitle_url="", subtitles=None, skip_intervals=None, aniskip_auto=True, audio_tracks=None,
            suppress_chapters=False):
        self.start()
        self.running = True
        if self.client:
            try:
                while True:
                    d = self.client.recv(8192)
                    if not d: break
            except (OSError, BlockingIOError):
                pass
        self.props["playback-time"] = 0
        self.props["duration"] = 0
        self.props["pause"] = False
        self.props["paused-for-cache"] = False
        self.props["percent-pos"] = 0
        self.max_playback_time = float(start_time or 0.0)
        self.file_loaded = False
        self.skip_intervals = skip_intervals or []
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
            self.send_cmd("set_property", "http-header-fields", ",".join(hf))

        # Override user's save-position-on-quit config to prevent cache hijacking
        self.send_cmd("set_property", "resume-playback", False)
        self.resume_time = start_time

        # If starting with an opening that begins at 0s, auto-seek resume_time to interval end
        skip_msg = ""
        if self.aniskip_auto and self.skip_intervals:
            first_skip = self.skip_intervals[0]
            if first_skip["start"] <= 2.0 and (start_time == 0 or start_time < first_skip["start"]):
                self.resume_time = first_skip["end"]
                self.skipped_intervals.add(0)
                from ..ui.player_screen import _fmt_time
                skip_msg = f"Skipped {first_skip.get('label', 'Opening')} ({_fmt_time(first_skip['start'])} → {_fmt_time(first_skip['end'])})\n\n"

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


        msg = f"{skip_msg}Now playing\n{title}\n\nShift+Right: Next  •  Shift+Left: Previous  •  Q: Quit"
        if osd_msg:
            msg += f"\n\n{osd_msg}"
        self.initial_osd_msg = msg

    def _attach_pending_external_tracks(self):
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
            for sub in self._pending_subtitles:
                sub_file = sub.get("url") or sub.get("file")
                sub_label = sub.get("label") or "Subtitle"
                if sub_file:
                    is_def = bool(sub.get("default") or sub_file == self._pending_subtitle_url)
                    mode = "select" if is_def else "auto"
                    self.send_cmd("sub-add", sub_file, mode, sub_label)
            self._pending_subtitles = []
            self._pending_subtitle_url = ""
        elif self._pending_subtitle_url:
            self.send_cmd("sub-add", self._pending_subtitle_url, "select")
            self._pending_subtitle_url = ""


    def quit(self):
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
        cleanup_mpv_runtime(self.runtime_dir)
        self.runtime_dir = None
        self.socket_path = None
        self.conf_path = None

    def wait_for_playback(self, ui_info, current_ep, total_eps, fetch_callback, is_binge=False):
        tty_fd = sys.stdin.fileno()
        if os.isatty(tty_fd):
            old_attrs = termios.tcgetattr(tty_fd)
            tty.setcbreak(tty_fd)
        else:
            old_attrs = None

        result = "QUIT"
        buf = ""
        want_skip_to = None
        want_skip_ep = None
        notify_prefetched = None
        countdown_active = False
        initial_osd_shown = False
        played_seconds = 0.0
        last_playback_tick = time.monotonic()
        playback_start_mono = time.monotonic()
        last_time_pos_change = playback_start_mono
        last_cache_flush = playback_start_mono
        file_loaded = False
        last_observed_playback_time = -1.0
        max_playback_time = float(getattr(self, "resume_time", 0) or 0.0)
        normal_stall_timeout = float(getattr(self, "normal_stall_timeout", 20.0) or 20.0)
        seek_stall_timeout = float(getattr(self, "seek_stall_timeout", 30.0) or 30.0)
        startup_timeout = float(getattr(self, "startup_timeout", 15.0) or 15.0)

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
                self.redraw_callback(self.props)

        def process_exited():
            try:
                return self.process is not None and self.process.poll() is not None
            except Exception:
                return False

        try:
            done = False
            pending_action = None
            while self.running and not done:
                now = time.monotonic()
                elapsed = max(0.0, now - last_playback_tick)
                last_playback_tick = now
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
                        self.expect_ghost_eof = True
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
                    key = sys.stdin.read(1)
                    if key == "\x03":
                        raise KeyboardInterrupt
                    if key.lower() == 'q':
                        pending_action = "QUIT"
                        self.send_cmd("stop")
                    elif key.lower() == 'n':
                        pending_action = "NEXT_MIRROR"
                        self.send_cmd("show-text", "Switching to next mirror...", 3000)
                        self.send_cmd("stop")
                    elif key in ('\t', 's', 'S') and not self.aniskip_auto and self.skip_intervals:
                        curr_time = self.props.get("playback-time", 0) or 0
                        for s_idx, s_item in enumerate(self.skip_intervals):
                            s_start = s_item["start"]
                            s_end = s_item["end"]
                            s_label = s_item.get("label", "Opening")
                            if s_start <= curr_time < s_end:
                                self.skipped_intervals.add(s_idx)
                                self.send_cmd("seek", s_end, "absolute")
                                self.send_cmd("show-text", f"Skipped {s_label} ({fmt_time(s_start)} → {fmt_time(s_end)})", 3000)
                                self.active_skip_prompt = None
                                break
                if self.client in r:
                    try:
                        data = self.client.recv(4096)
                        if not data:
                            if pending_action:
                                result = pending_action
                            elif not file_loaded and (now - playback_start_mono) < startup_timeout:
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
                                ev = msg.get("event")
                                if ev == "file-loaded":
                                    file_loaded = True
                                    self.file_loaded = True
                                    last_time_pos_change = now
                                    last_cache_flush = now
                                    self._attach_pending_external_tracks()
                                    if self.skip_intervals:
                                        self.send_cmd(
                                            "script-message",
                                            "set_skip_intervals",
                                            json.dumps(self.skip_intervals),
                                            "yes" if self.aniskip_auto else "no",
                                        )
                                elif ev in ("playback-restart", "seek"):
                                    file_loaded = True
                                    self.file_loaded = True
                                    last_cache_flush = now
                                    last_time_pos_change = now
                                elif ev == "end-file":
                                    if getattr(self, "expect_ghost_eof", False):
                                        self.expect_ghost_eof = False
                                        continue

                                    reason = msg.get("reason")
                                    dur = float(self.props.get("duration", 0) or 0)
                                    curr_pos = max_playback_time

                                    if pending_action:
                                        result = pending_action
                                        if pending_action == "QUIT":
                                            self.running = False
                                    elif reason == "error" and process_exited():
                                        result = "QUIT"
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
                                            self.send_cmd("show-text", "⚠ Playback failed. Trying next mirror...", 10000)
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
                                            self.send_cmd("show-text", "⚠ Stream ended prematurely. Trying next mirror...", 10000)
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
                                    if name in self.props:
                                        if val is not None:
                                            self.props[name] = val
                                        redraw()

                                        if name == "playback-time" and val is not None:
                                            curr_val = float(val or 0)
                                            if curr_val > 0.0:
                                                file_loaded = True
                                                self.file_loaded = True
                                            if last_observed_playback_time < 0 or abs(curr_val - last_observed_playback_time) >= 0.01:
                                                last_time_pos_change = now
                                                if last_observed_playback_time >= 0 and abs(curr_val - last_observed_playback_time) > 2.0:
                                                    last_cache_flush = now
                                                last_observed_playback_time = curr_val
                                            if curr_val > max_playback_time:
                                                max_playback_time = curr_val
                                        elif name == "pause":
                                            last_time_pos_change = now
                                            last_cache_flush = now
                                        elif name == "paused-for-cache" and val:
                                            last_cache_flush = now
                                        elif name in ("duration", "percent-pos") and float(val or 0) > 0:
                                            file_loaded = True
                                            self.file_loaded = True

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
                                                s_end = s_item["end"]
                                                s_label = s_item.get("label", "Opening")
                                                if s_start <= curr_time < (s_end - 0.5):
                                                    if s_idx not in self.skipped_intervals:
                                                        if self.aniskip_auto:
                                                            self.skipped_intervals.add(s_idx)
                                                            self._last_seek_mono = time.monotonic()
                                                            self.send_cmd("seek", s_end, "absolute")
                                                            self.send_cmd("show-text", f"Skipped {s_label} ({fmt_time(s_start)} → {fmt_time(s_end)})", 3000)
                                                            self.active_skip_prompt = None
                                                        else:
                                                            if self.active_skip_prompt != s_idx:
                                                                self.active_skip_prompt = s_idx
                                                                self.send_cmd("show-text", f"[Tab/s] Skip {s_label} ({fmt_time(s_start)} → {fmt_time(s_end)})", int(max(1.0, (s_end - curr_time)) * 1000))
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
                                                    self.send_cmd("show-text", "Preparing next episode...", 3000)
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
                                                        self.send_cmd("show-text", f"Next up\n{ntitle}\nStarts in 0:{int(rem_sec):02d}", 60000)
                                                        countdown_active = True
                                                    elif rem_sec <= 5:
                                                        self.send_cmd("show-text", "Starting next episode...", 60000)
                                                        countdown_active = True
                                                    elif countdown_active:
                                                        self.send_cmd("show-text", "")
                                                        countdown_active = False

                                                if rem_sec <= 5 and self.prefetched_stream:
                                                    self.expect_ghost_eof = True
                                                    result = "NEXT"
                                                    done = True
                                                    break

                                elif ev == "client-message":
                                    args = msg.get("args", [])
                                    if args:
                                        if args[0] in ("skip_interval", "skip_op", "skip_ed") and self.skip_intervals:
                                            curr_time = self.props.get("playback-time", 0) or 0
                                            for s_idx, s_item in enumerate(self.skip_intervals):
                                                s_start = s_item["start"]
                                                s_end = s_item["end"]
                                                s_label = s_item.get("label", "Opening")
                                                if s_start <= curr_time < s_end:
                                                    self.skipped_intervals.add(s_idx)
                                                    self.send_cmd("seek", s_end, "absolute")
                                                    self.send_cmd("show-text", f"Skipped {s_label} ({fmt_time(s_start)} → {fmt_time(s_end)})", 3000)
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
                                        elif args[0] == "next_mirror":
                                            pending_action = "NEXT_MIRROR"
                                            self.send_cmd("show-text", "Switching to next mirror...", 3000)
                                            self.send_cmd("stop")
                            except Exception: pass
                    except BlockingIOError: pass

                # 1. Check Startup Timeout (Dead link / network hang during initial connect)
                if not file_loaded and (now - playback_start_mono) > startup_timeout:
                    result = "ERROR"
                    self.send_cmd("show-text", "⚠ Mirror connection timed out. Trying next mirror...", 5000)
                    done = True
                    break

                # 2. Check Ground Truth Stall Invariant (unpaused & clock frozen)
                if file_loaded:
                    is_user_paused = bool(self.props.get("pause", False))
                    if is_user_paused:
                        # While paused, keep both timers fresh so we don't fire on resume.
                        last_time_pos_change = now
                        last_cache_flush = now
                    else:
                        # in_grace: we recently seeked/flushed — give the stream more time.
                        in_grace = (now - last_cache_flush) < seek_stall_timeout
                        active_timeout = seek_stall_timeout if in_grace else normal_stall_timeout
                        if (now - last_time_pos_change) >= active_timeout:
                            result = "ERROR"
                            self.send_cmd("show-text", "⚠ Stream stalled. Switching mirror...", 5000)
                            done = True
                            break

                redraw()
        finally:
            if old_attrs: termios.tcsetattr(tty_fd, termios.TCSADRAIN, old_attrs)
            print()

        if max_playback_time > (float(self.props.get("playback-time") or 0)):
            self.props["playback-time"] = max_playback_time

        return result, played_seconds
