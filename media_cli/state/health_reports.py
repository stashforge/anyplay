"""Store diagnostic reports without loading media or migrating application state."""

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ..constants import APP_NAME

MAX_REPORT_BYTES = 8 * 1024 * 1024
_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}\Z")


@dataclass(frozen=True)
class SavedReportSummary:
    run_id: str
    started_at: datetime
    status: str
    verification: str


def reports_directory() -> Path:
    # Match state.paths without importing its legacy-directory migration side effect.
    return Path.home() / ".local" / "state" / APP_NAME / "health" / "reports"


def _read_regular_file(path: Path) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise ValueError("Missing or invalid report file")
    with path.open("rb") as stream:
        content = stream.read(MAX_REPORT_BYTES + 1)
    if len(content) > MAX_REPORT_BYTES:
        raise ValueError("Report exceeds read limit")
    return content


def _read_summary(folder: Path) -> SavedReportSummary:
    if folder.is_symlink() or not _RUN_ID.fullmatch(folder.name):
        raise ValueError("Invalid report location")
    try:
        data = json.loads(_read_regular_file(folder / "report.json"))
    except RecursionError as error:
        raise ValueError("Invalid report metadata") from error
    return _summary_from_data(data, folder.name)


def _summary_from_data(data, run_id: str) -> SavedReportSummary:
    try:
        if not isinstance(data, dict):
            raise ValueError("Invalid report metadata")
        if type(data.get("schema_version")) is not int or data["schema_version"] != 1:
            raise ValueError("Unsupported report version")
        if data.get("run_id") != run_id:
            raise ValueError("Report identity mismatch")
        status, level = data.get("status"), data.get("verification")
        if status not in ("complete", "incomplete", "cancelled"):
            raise ValueError("Invalid run status")
        if level not in ("catalogue", "discovery", "extraction", "http", "mpv", "visible", "mixed"):
            raise ValueError("Invalid verification level")
        checked = datetime.fromisoformat(data["started_at"].replace("Z", "+00:00"))
        if checked.utcoffset() is None:
            raise ValueError("Missing report timezone")
        checked.astimezone()
        return SavedReportSummary(run_id, checked, status, level)
    except (TypeError, KeyError, AttributeError, RecursionError, OverflowError) as error:
        raise ValueError("Invalid report metadata") from error


def list_saved_reports(directory: Path) -> tuple[list[SavedReportSummary], int]:
    """Return newest summaries and skipped count; never create or repair files."""
    if directory.is_symlink():
        raise OSError("Report directory is a symbolic link")
    try:
        folders = list(directory.iterdir())
    except FileNotFoundError:
        return [], 0
    rows = []
    skipped = 0
    for folder in folders:
        if folder.name.startswith(".pending-"):
            continue
        try:
            if folder.is_symlink():
                skipped += 1
                continue
            if not folder.is_dir():
                continue
            rows.append(_read_summary(folder))
        except (OSError, ValueError):
            skipped += 1
    rows.sort(key=lambda row: (row.started_at, row.run_id), reverse=True)
    return rows, skipped


def _resolve_saved_report(directory: Path, selector: str) -> Path:
    if directory.is_symlink():
        raise ValueError("Invalid report directory")
    if selector == "latest":
        rows, _ = list_saved_reports(directory)
        if not rows:
            raise FileNotFoundError("No saved health reports")
        selector = rows[0].run_id
    if not _RUN_ID.fullmatch(selector):
        raise ValueError("Invalid report ID")
    folder = directory / selector
    if not folder.exists():
        raise FileNotFoundError("Report not found")
    _read_summary(folder)
    return folder


def read_saved_report(directory: Path, selector: str) -> str:
    """Read an explicitly selected report, without accepting paths as run IDs."""
    folder = _resolve_saved_report(directory, selector)
    text = _read_regular_file(folder / "report.txt").decode("utf-8")
    # Render structured evidence on read; published report files stay immutable.
    from ..domain.health_report import format_health_report, prepare_health_evidence
    import shutil

    try:
        data = json.loads(_read_regular_file(folder / "report.json"))
        if isinstance(data.get("providers"), list):
            clean = prepare_health_evidence(data)
            clean.update(run_id=folder.name, schema_version=1)
            if isinstance(data.get("saved_at"), str):
                from ..core.terminal import sanitize_terminal_text
                clean["saved_at"] = sanitize_terminal_text(data["saved_at"])
            return format_health_report(
                clean, width=shutil.get_terminal_size(fallback=(80, 24)).columns
            )
    except (ValueError, TypeError, RecursionError):
        pass  # Legacy text remains readable when structured evidence is absent.
    # Report text is data, never a source of terminal escape/control commands.
    text = re.sub(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[0-?]*[ -/]*[@-~]|\x1b[@-_]", "", text)
    text = text.replace("\r\n", "\n")
    return "".join(char for char in text if char in "\n\t" or char.isprintable())


def delete_saved_report(directory: Path, selector: str) -> str:
    """Delete one validated report's known files, never recursively remove a tree."""
    folder = _resolve_saved_report(directory, selector)
    files = list(folder.iterdir())
    for path in files:
        if path.name not in ("report.json", "report.txt") or path.is_symlink() or not path.is_file():
            raise ValueError("Report contains unexpected files; nothing deleted")
    # Keep metadata until the readable report is removed, so interrupted cleanup
    # remains identifiable. Listings derive their state from remaining reports.
    text = folder / "report.txt"
    if text.exists():
        text.unlink()
    (folder / "report.json").unlink()
    folder.rmdir()
    return folder.name


def save_health_report(directory: Path, report: dict) -> str:
    """Publish a private JSON/text pair, leaving no visible half-written report."""
    import os
    import shutil
    import tempfile
    import uuid
    from datetime import timezone

    from ..domain.health_report import format_health_report, prepare_health_evidence
    from .io import write_private_text

    data = prepare_health_evidence(report)
    now = datetime.now(timezone.utc)
    run_id = now.strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:12]
    data.update(schema_version=1, run_id=run_id, saved_at=now.isoformat())
    summary = _summary_from_data(data, run_id)
    data["started_at"] = summary.started_at.astimezone(timezone.utc).isoformat()
    content = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    readable = format_health_report(data)
    if max(len(content.encode("utf-8")), len(readable.encode("utf-8"))) > MAX_REPORT_BYTES:
        raise ValueError("Report exceeds read limit")
    if directory.is_symlink():
        raise ValueError("Invalid report directory")
    directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    destination = directory / run_id
    if destination.exists():
        raise FileExistsError("Report ID already exists")
    staging = Path(tempfile.mkdtemp(prefix=".pending-", dir=directory))
    try:
        write_private_text(staging, "report.json", content)
        write_private_text(staging, "report.txt", readable)
        os.rename(staging, destination)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return run_id
