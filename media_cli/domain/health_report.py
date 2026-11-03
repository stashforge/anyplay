"""Validate and format diagnostic evidence, never perform provider checks."""

import hashlib
import json
import math
import re
import secrets
from urllib.parse import urlsplit

from ..core.terminal import sanitize_terminal_text, strip_ansi

TITLE_DETAIL_FIELDS = ("title", "alternate_title", "cover", "description", "format", "status", "year", "mal_id", "anilist_id")

_PROVIDER_STATUSES = (
    "working", "partly_working", "blocked", "missing_dependency", "inconclusive",
    "broken", "not_checked",
)
_STAGE_STATUSES = (
    "passed", "failed", "skipped", "not_applicable", "not_verified", "inconclusive", "blocked",
)
_VERIFICATION_LEVELS = ("catalogue", "discovery", "extraction", "http", "mpv", "visible")
_TRANSLATIONS = ("sub", "dub", "both")


def _terminal_label(value) -> str:
    if not isinstance(value, str):
        raise ValueError("Report labels must be text")
    # Normalize C1 escape forms before using the shared terminal sanitizer.
    value = value.translate({code: "\x1b" + chr(code - 64) for code in range(128, 160)})
    value = strip_ansi(value)
    # Remove embedded controls rather than allowing them to split credential names.
    value = "".join(c for c in value if c.isprintable())
    return sanitize_terminal_text(value)


def _safe_label(value) -> str:
    value = _terminal_label(value)
    def host_only(match):
        try:
            return urlsplit(match.group(0)).hostname or "[URL omitted]"
        except ValueError:
            return "[URL omitted]"
    value = re.sub(r"https?://\S+", host_only, value, flags=re.IGNORECASE)
    value = re.sub(r"(?i)\bbearer\s+\S+", "Bearer [redacted]", value)
    value = re.sub(r"(?im)\b(?:authorization|set-cookie|cookie)\s*:[^\r\n]*", "[redacted]", value)
    value = re.sub(r"(?i)\b(?:token|cf_clearance|signature|password|secret|api_key)\s*[:=]\s*\S+", "[redacted]", value)
    return " ".join(value.split())


def _safe_identifier(value, *, provider_id: str, kind: str, key: bytes) -> str:
    """URL references correlate evidence only; they cannot reproduce a request."""
    terminal_safe = _terminal_label(value)
    if re.search(r"[a-z][a-z0-9+.-]*://|^//", terminal_safe, re.IGNORECASE):
        # Hash the original, not its redacted host. The key lives for one preparation
        # only and is never saved, preventing cross-report URL guessing/linking.
        payload = json.dumps([provider_id, kind, value], ensure_ascii=True).encode("utf-8")
        digest = hashlib.blake2b(payload, key=key, digest_size=16).hexdigest()
        return "opaque-url-ref:" + digest
    return _safe_label(value)


def prepare_health_evidence(report: dict) -> dict:
    """Copy only report fields; request headers, raw URLs and tokens are excluded."""
    if not isinstance(report, dict) or not isinstance(report.get("providers"), list):
        raise ValueError("Report needs a provider list")
    identity_key = secrets.token_bytes(32)
    result = {key: report.get(key) for key in ("started_at", "status", "verification")}
    if result["verification"] not in (*_VERIFICATION_LEVELS, "mixed"):
        raise ValueError("Invalid verification level")
    if "translation" in report:
        if report["translation"] not in _TRANSLATIONS:
            raise ValueError("Invalid requested translation")
        result["translation"] = report["translation"]
    if "seed" in report:
        if type(report["seed"]) is not int:
            raise ValueError("Invalid sampling seed")
        result["seed"] = report["seed"]
    providers = []
    for provider in report["providers"]:
        if not isinstance(provider, dict) or provider.get("status") not in _PROVIDER_STATUSES:
            raise ValueError("Invalid provider result")
        stages = provider.get("stages")
        if not isinstance(stages, list):
            raise ValueError("Provider result needs stage evidence")
        clean = {key: _safe_label(provider.get(key, "")) for key in ("id", "name")}
        if not clean["id"]:
            raise ValueError("Provider ID is required")
        clean["status"] = provider["status"]
        for key, allowed in (
            ("verification", _VERIFICATION_LEVELS),
            ("run_status", ("complete", "incomplete", "cancelled", "not_started")),
            ("translation", _TRANSLATIONS),
        ):
            if key in provider:
                if provider[key] not in allowed:
                    raise ValueError(f"Invalid provider {key}")
                clean[key] = provider[key]
        if "scope_note" in provider:
            clean["scope_note"] = _safe_label(provider["scope_note"])
        if "local_issues" in provider:
            if not isinstance(provider["local_issues"], list):
                raise ValueError("Invalid local requirement evidence")
            clean["local_issues"] = []
            for issue in provider["local_issues"]:
                if not isinstance(issue, dict) or issue.get("kind") not in ("python", "executable", "runtime", "companion", "dependency"):
                    raise ValueError("Invalid local requirement")
                clean["local_issues"].append({"kind": issue["kind"], "stage": _safe_label(issue.get("stage", "")),
                                              "reason": _safe_label(issue.get("reason", ""))})
        clean["stages"] = []
        for stage in stages:
            if not isinstance(stage, dict) or stage.get("status") not in _STAGE_STATUSES:
                raise ValueError("Invalid stage result")
            item = {"status": stage["status"], "name": _safe_label(stage.get("name", ""))}
            if not item["name"]:
                raise ValueError("Stage name is required")
            if "required" in stage:
                if type(stage["required"]) is not bool:
                    raise ValueError("Invalid required-track flag")
                item["required"] = stage["required"]
            if "issue_scope" in stage:
                if stage["issue_scope"] not in ("local", "unknown", "provider"):
                    raise ValueError("Invalid issue scope")
                item["issue_scope"] = stage["issue_scope"]
            for key in ("title", "age_group", "episode", "source", "mirror", "reason", "translation", "track", "subtitle_label", "host", "provider_title_id", "channel", "match_method", "evidence_origin"):
                if key in stage:
                    if key in ("provider_title_id", "episode"):
                        item[key] = _safe_identifier(
                            stage[key], provider_id=provider["id"], kind=key, key=identity_key
                        )
                    else:
                        item[key] = _safe_label(stage[key])
            for key in ("mal_id", "release_year", "episode_count", "source_count", "mirror_count", "unresolved_count", "http_status", "bytes_sampled", "mirror_index", "decoded_frames", "decoded_samples", "anilist_id", "channel_count"):
                if key in stage:
                    if type(stage[key]) is not int or stage[key] < 0:
                        raise ValueError("Invalid numeric stage evidence")
                    item[key] = stage[key]
            for key in ("source_names", "channel_names"):
                if key in stage:
                    if not isinstance(stage[key], list):
                        raise ValueError("Invalid source/channel names")
                    item[key] = [_safe_label(name) for name in stage[key]]
            for key in ("present_fields", "missing_fields"):
                if key in stage:
                    if not isinstance(stage[key], list) or any(field not in TITLE_DETAIL_FIELDS for field in stage[key]):
                        raise ValueError("Invalid title-details fields")
                    item[key] = list(dict.fromkeys(stage[key]))
            if "mirrors" in stage:
                if not isinstance(stage["mirrors"], list):
                    raise ValueError("Invalid mirror evidence")
                item["mirrors"] = []
                for mirror in stage["mirrors"]:
                    if not isinstance(mirror, dict):
                        raise ValueError("Invalid mirror descriptor")
                    clean_mirror = {key: _safe_label(mirror.get(key, "")) for key in ("resolution", "type", "host")}
                    for key in ("source", "channel"):
                        if key in mirror:
                            clean_mirror[key] = _safe_label(mirror[key])
                    for key in ("separate_audio", "headers_present"):
                        if type(mirror.get(key)) is not bool:
                            raise ValueError("Invalid mirror track evidence")
                        clean_mirror[key] = mirror[key]
                    count = mirror.get("subtitle_count")
                    if type(count) is not int or count < 0:
                        raise ValueError("Invalid subtitle count")
                    clean_mirror["subtitle_count"] = count
                    item["mirrors"].append(clean_mirror)
            if "elapsed_seconds" in stage:
                elapsed = stage["elapsed_seconds"]
                try:
                    valid_elapsed = type(elapsed) in (int, float) and math.isfinite(elapsed) and elapsed >= 0
                except OverflowError:
                    valid_elapsed = False
                if not valid_elapsed:
                    raise ValueError("Invalid stage duration")
                item["elapsed_seconds"] = elapsed
            clean["stages"].append(item)
        if clean["status"] == "working" and not any(s["status"] == "passed" for s in clean["stages"]):
            raise ValueError("Working result requires passed stage evidence")
        providers.append(clean)
    result["providers"] = providers
    return result


def format_health_report(data: dict, *, width: int = 80) -> str:
    """Render recorded evidence without changing checks or verdicts."""
    from .health_report_text import render_health_report

    return render_health_report(data, width=width)
