"""Custom opening/ending skip forms and provider matching."""

from __future__ import annotations

import re
import os
import shlex
import shutil
import subprocess
import tempfile
from typing import Any


class SkipFormError(ValueError):
    """Raised when the editor form cannot be parsed."""


class SkipEditorError(RuntimeError):
    """Raised when no usable editor can be launched."""


def _norm_provider(value: str) -> str:
    return str(value or "").strip().casefold()


def _split_csv(value: Any, *, default: list[str] | None = None) -> list[str]:
    if value is None:
        return list(default or [])
    if isinstance(value, (list, tuple, set)):
        raw = value
    else:
        raw = str(value).replace(";", ",").split(",")
    items = []
    for item in raw:
        key = _norm_provider(str(item))
        if key and key not in items:
            items.append(key)
    return items or list(default or [])


def parse_time(value: str) -> float:
    raw = str(value or "").strip().lower()
    if not raw:
        raise SkipFormError("empty timestamp")
    if raw.endswith("ms"):
        raise SkipFormError(f"invalid timestamp {value!r}")
    if raw.endswith("s"):
        try:
            return float(raw[:-1])
        except ValueError as exc:
            raise SkipFormError(f"invalid timestamp {value!r}") from exc
    if raw.endswith("m"):
        try:
            return float(raw[:-1]) * 60.0
        except ValueError as exc:
            raise SkipFormError(f"invalid timestamp {value!r}") from exc
    if ":" in raw:
        parts = raw.split(":")
        if len(parts) not in (2, 3):
            raise SkipFormError(f"invalid timestamp {value!r}")
        try:
            nums = [float(part) for part in parts]
        except ValueError as exc:
            raise SkipFormError(f"invalid timestamp {value!r}") from exc
        if any(num < 0 for num in nums):
            raise SkipFormError(f"invalid negative timestamp {value!r}")
        if len(nums) == 2:
            minutes, seconds = nums
            return minutes * 60.0 + seconds
        hours, minutes, seconds = nums
        return hours * 3600.0 + minutes * 60.0 + seconds
    try:
        value_f = float(raw)
    except ValueError as exc:
        raise SkipFormError(f"invalid timestamp {value!r}") from exc
    if value_f < 0:
        raise SkipFormError(f"invalid negative timestamp {value!r}")
    # In this editor, bare numbers are intentionally minutes.
    return value_f * 60.0


def _parse_interval(kind: str, value: str) -> dict | None:
    raw = str(value or "").strip()
    if not raw or raw.lower() in {"none", "off", "-"}:
        return None
    parts = raw.split()
    if len(parts) > 2:
        raise SkipFormError(f"{kind}: expected one or two timestamps")
    try:
        if kind == "opening":
            if len(parts) == 1:
                start, end = 0.0, parse_time(parts[0])
            else:
                start, end = parse_time(parts[0]), parse_time(parts[1])
            label, stype = "Opening", "op"
        else:
            start = parse_time(parts[0])
            end = parse_time(parts[1]) if len(parts) == 2 else None
            label, stype = "Ending", "ed"
    except SkipFormError as exc:
        raise SkipFormError(f"{kind}: {exc}") from exc
    if end is not None and end <= start:
        raise SkipFormError(f"{kind}: end time must be after start time")
    if kind == "opening" and (end is None or end <= 0):
        raise SkipFormError("opening: end time must be greater than zero")
    return {"type": stype, "label": label, "start": float(start), "end": None if end is None else float(end)}


def _strip_inline_comment(line: str) -> str:
    # Keep this intentionally simple; URLs are not expected in the form values.
    return line.split("#", 1)[0].strip()


def parse_skip_form(text: str) -> dict:
    values: dict[str, str] = {}
    for lineno, line in enumerate(str(text or "").splitlines(), start=1):
        clean = _strip_inline_comment(line)
        if not clean:
            continue
        if "=" not in clean:
            raise SkipFormError(f"line {lineno}: expected key = value")
        key, value = clean.split("=", 1)
        key = key.strip().casefold()
        if key not in {"providers", "exclude_providers", "mode", "opening", "ending"}:
            raise SkipFormError(f"line {lineno}: unknown key {key!r}")
        values[key] = value.strip()

    mode = str(values.get("mode") or "session").strip().casefold()
    if mode not in {"session", "title"}:
        raise SkipFormError("mode must be session or title")

    intervals = []
    for kind in ("opening", "ending"):
        interval = _parse_interval(kind, values.get(kind, "none"))
        if interval:
            intervals.append(interval)

    return {
        "mode": mode,
        "providers": _split_csv(values.get("providers"), default=["all"]),
        "exclude_providers": _split_csv(values.get("exclude_providers"), default=[]),
        "intervals": intervals,
    }


def _fmt_time(seconds: float | None) -> str:
    if seconds is None:
        return "none"
    total = int(round(float(seconds)))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _interval_value(entry: dict, kind: str) -> str:
    for item in entry.get("intervals") or []:
        if item.get("type") == kind:
            start = float(item.get("start") or 0)
            end = item.get("end")
            if kind == "op" and start <= 0:
                return _fmt_time(end)
            if end is None:
                return _fmt_time(start)
            return f"{_fmt_time(start)} {_fmt_time(end)}"
    return "none"


def render_skip_form(
    *,
    title: str,
    providers: list[str],
    entry: dict | None = None,
    error: str = "",
) -> str:
    entry = entry or {}
    mode = entry.get("mode") or "session"
    provider_value = ", ".join(entry.get("providers") or ["all"])
    exclude_value = ", ".join(entry.get("exclude_providers") or ["torrents-csv"])
    available = ", ".join(providers)
    lines = []
    if error:
        lines.extend([f"# ERROR: {error}", "# Fix the form, save, and exit.", ""])
    lines.extend(
        [
            f"# Custom skips for: {title}",
            "# Comments are regenerated every time; only key/value data is saved.",
            f"# Available providers: {available}",
            "",
            "# Applies to provider(s). Use all, or comma-separated provider ids/names.",
            "# Examples: all | mkissa | anizone,anidb",
            f"providers = {provider_value}",
            "",
            "# Providers to exclude even when providers = all.",
            "# Default excludes torrents-csv because torrent release timings often differ.",
            f"exclude_providers = {exclude_value}",
            "",
            "# Save scope for this title. Use session or title.",
            f"mode = {mode}",
            "",
            "# Opening skip. One value means 00:00 -> value. Two values mean start -> end.",
            "# Examples: 1:30 | 0:12 1:42 | none",
            f"opening = {_interval_value(entry, 'op')}",
            "",
            "# Ending skip. One value means value -> end of video. Two values mean start -> end.",
            "# Examples: 22:10 | 22:10 23:40 | none",
            f"ending = {_interval_value(entry, 'ed')}",
            "",
        ]
    )
    return "\n".join(lines)


def resolve_editor(config: dict | None = None) -> list[str]:
    config = config or {}
    candidates = []
    configured = str(config.get("editor") or "").strip()
    if configured:
        candidates.append(configured)
    candidates.extend(
        str(os.environ.get(name) or "").strip()
        for name in ("VISUAL", "EDITOR")
    )
    candidates.extend(["nano", "vim", "vi"])

    for candidate in candidates:
        if not candidate:
            continue
        parts = shlex.split(candidate)
        if not parts:
            continue
        exe = parts[0]
        if os.path.isabs(exe) and os.access(exe, os.X_OK):
            return parts
        if shutil.which(exe):
            return parts
    raise SkipEditorError("No editor found. Set editor in config, or set VISUAL/EDITOR.")


def edit_skip_form(
    *,
    title: str,
    providers: list[str],
    entry: dict | None,
    config: dict | None,
) -> dict:
    editor_cmd = resolve_editor(config)
    fd, path = tempfile.mkstemp(prefix="anyplay-custom-skips-", suffix=".conf", text=True)
    os.close(fd)
    try:
        content = render_skip_form(title=title, providers=providers, entry=entry)
        while True:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(content)
            result = subprocess.run(editor_cmd + [path], check=False)
            if result.returncode != 0:
                raise SkipEditorError(f"Editor exited with code {result.returncode}")
            with open(path, encoding="utf-8") as handle:
                edited = handle.read()
            try:
                return parse_skip_form(edited)
            except SkipFormError as exc:
                content = render_skip_form(
                    title=title,
                    providers=providers,
                    entry=_form_values_to_entry(edited, fallback=entry),
                    error=str(exc),
                )
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def _form_values_to_entry(text: str, *, fallback: dict | None = None) -> dict:
    values = {}
    for line in str(text or "").splitlines():
        clean = _strip_inline_comment(line)
        if "=" in clean:
            key, value = clean.split("=", 1)
            values[key.strip().casefold()] = value.strip()
    entry = dict(fallback or {})
    entry["mode"] = values.get("mode") or entry.get("mode") or "session"
    entry["providers"] = _split_csv(values.get("providers"), default=entry.get("providers") or ["all"])
    entry["exclude_providers"] = _split_csv(
        values.get("exclude_providers"),
        default=entry.get("exclude_providers") or ["torrents-csv"],
    )
    intervals = []
    for kind, stype, label in (("opening", "op", "Opening"), ("ending", "ed", "Ending")):
        try:
            interval = _parse_interval(kind, values.get(kind, "none"))
        except SkipFormError:
            interval = None
        if interval:
            intervals.append(interval)
        elif "intervals" in entry:
            intervals.extend(
                item for item in entry.get("intervals") or [] if item.get("type") == stype and kind not in values
            )
    entry["intervals"] = intervals
    return entry


def provider_matches(entry: dict, provider_id: str) -> bool:
    provider = _norm_provider(provider_id)
    providers = _split_csv(entry.get("providers"), default=["all"])
    excludes = set(_split_csv(entry.get("exclude_providers"), default=[]))
    if provider in excludes:
        return False
    return "all" in providers or provider in providers


def pick_provider_entry(entries: list[dict] | None, provider_id: str) -> dict | None:
    matches = [entry for entry in (entries or []) if entry.get("intervals") and provider_matches(entry, provider_id)]
    if not matches:
        return None
    provider = _norm_provider(provider_id)
    specific = [entry for entry in matches if provider in _split_csv(entry.get("providers"), default=[])]
    return specific[-1] if specific else matches[-1]


def stable_title_keys(show: dict | None, fallback_show_id: str | None = None) -> list[str]:
    show = show or {}
    keys = []
    for raw in (
        fallback_show_id,
        show.get("_id"),
        show.get("id"),
        show.get("aniListId"),
        show.get("malId"),
        show.get("name"),
        show.get("englishName"),
    ):
        if raw is None:
            continue
        key = str(raw).strip()
        if key and key not in keys:
            keys.append(key)
    return keys


def compact_intervals(intervals: list[dict]) -> list[dict]:
    return [
        {
            "type": item.get("type") or "",
            "label": item.get("label") or ("Opening" if item.get("type") == "op" else "Ending"),
            "start": float(item.get("start") or 0),
            "end": None if item.get("end") is None else float(item.get("end")),
        }
        for item in intervals or []
    ]


def resolve_custom_skips(
    *,
    show: dict | None,
    fallback_show_id: str | None,
    provider_id: str,
    session_store: dict | None,
    saved_entries: list[dict] | None,
) -> dict | None:
    keys = stable_title_keys(show, fallback_show_id)
    session_store = session_store or {}
    session_entries = []
    for key in keys:
        entries = session_store.get(str(key))
        if isinstance(entries, list):
            session_entries = entries
            break
    entry = pick_provider_entry(session_entries, provider_id)
    if entry:
        return {"mode": "session", "entry": entry, "intervals": compact_intervals(entry.get("intervals") or [])}
    entry = pick_provider_entry(saved_entries, provider_id)
    if entry:
        return {"mode": "title", "entry": entry, "intervals": compact_intervals(entry.get("intervals") or [])}
    return None
