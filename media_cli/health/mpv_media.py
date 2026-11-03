"""Opt-in silent decode sampling; independent of normal playback state."""

import os
import re
import subprocess
import tempfile
import time

MPV_BUDGET = 40
SAMPLE_SECONDS = 3
LOG_LIMIT = 1024 * 1024


def decode_evidence(text, *, returncode, timed_out=False):
    """Only decoded filter frames count, never track headers or a clean exit."""
    video = len(re.findall(r'^\[ffmpeg\] Parsed_showinfo_\d+: n:\s*\d+\s+pts:', text, re.MULTILINE))
    audio = sum(int(n) for n in re.findall(r'^\[ffmpeg\] Parsed_ashowinfo_\d+: n:\d+ .*?nb_samples:(\d+)', text, re.MULTILINE))
    checks = []
    for track, count, key in (("video", video, "decoded_frames"), ("audio", audio, "decoded_samples")):
        reason = ("Decoded video frames observed; visual quality not verified" if track == "video" else
                  "Decoded audio samples observed; audible output not verified") if count else (
                  "Decode time budget exceeded without track evidence; not proof of a dead mirror" if timed_out else
                  "No decoded track evidence; player exit alone is insufficient")
        checks.append({"track": track, "required": True, "status": "passed" if count else "inconclusive", "reason": reason, key: count})
    return checks


def run_sample(executable, url, *, timeout=MPV_BUDGET):
    command = [executable, "--no-config", "--load-scripts=no", "--no-ytdl", "--autoload-files=no",
               "--vo=null", "--ao=null", "--hwdec=no", "--input-terminal=no", "--no-resume-playback",
               "--save-position-on-quit=no", "--keep-open=no", "--idle=no", "--sid=no",
               f"--length={SAMPLE_SECONDS}", "--cache=no", "--demuxer-readahead-secs=1",
               "--demuxer-max-bytes=4MiB", "--demuxer-max-back-bytes=0", "--network-timeout=8",
               "--vf=lavfi=[showinfo]", "--af=lavfi=[ashowinfo]", "--msg-level=all=no,ffmpeg=debug", "--", url]
    before = time.monotonic()
    timed_out = False
    # Logs may contain upstream URLs. Keep them private and transient, never report them.
    with tempfile.TemporaryFile() as log:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
        try:
            while process.poll() is None:
                if time.monotonic()-before >= timeout or os.fstat(log.fileno()).st_size > LOG_LIMIT:
                    timed_out = True
                    break
                time.sleep(0.05)
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()
        log.seek(0)
        checks = decode_evidence(log.read(LOG_LIMIT).decode('utf-8', errors='replace'),
                                 returncode=process.returncode, timed_out=timed_out)
    for check in checks:
        check['elapsed_seconds'] = time.monotonic()-before
    return checks


def probe_entries(entries):
    from ..services.requirements import require_executable
    from ..media.local_proxy import start_local_proxy, stop_local_proxy

    executable = require_executable('mpv', 'Health decoding')
    if not entries:
        return []
    from ..media.sources import stream_quality_preference_key
    index, entry = min(enumerate(entries), key=lambda row: stream_quality_preference_key(row[1], "best"))
    before = time.monotonic()
    url, server = start_local_proxy(entry.get('streamUrl') or entry.get('sourceUrl') or entry.get('link'),
                                    entry.get('referer') or '', entry.get('headers') or {},
                                    timeout=8, stream_type=entry.get('type') or 'mp4', title='health-sample',
                                    audio_url=entry.get('audio_url'), audio_tracks=entry.get('audio_tracks'), subtitles=None, target_player='mpv',
                                    flixcloud_w_payload=entry.get('flixcloud_w_payload'), flixcloud_xor_mask=entry.get('flixcloud_xor_mask'),
                                    ip_version=entry.get('ip_version'))
    try:
        checks = run_sample(executable, url, timeout=max(0, MPV_BUDGET-(time.monotonic()-before)))
        for check in checks:
            check.update(mirror_index=index, mirror=str(entry.get('resolution') or 'auto'))
        return checks
    finally:
        stop_local_proxy(server)
