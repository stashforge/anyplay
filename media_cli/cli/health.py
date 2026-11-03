"""Lightweight health-report commands; this module does not run provider checks."""

import sys
import os
import shlex
import subprocess

from ..state import health_reports
from ..health.profiles import PROFILES


def show_report(text: str, *, plain: bool = False) -> int:
    if not plain and sys.stdin.isatty() and sys.stdout.isatty():
        try:
            pager = shlex.split(os.environ.get("PAGER", "")) or ["less", "-+F", "-+X"]
            try:
                print("\033[?25l", end="", flush=True)
                result = subprocess.run(pager, input=text, text=True, check=False)
            finally:
                print("\033[?25h", end="", flush=True)
            if result.returncode == 0:
                return 0
            print("Pager exited unsuccessfully; showing plain report.", file=sys.stderr)
        except (OSError, ValueError):
            print("Pager unavailable; showing plain report.", file=sys.stderr)
    print(text, end="" if text.endswith("\n") else "\n")
    return 0


def handle_health_command(action: str, report_id: str | None = None, *, plain: bool = False, delete: bool = False, seed: int | None = None, verify: str | None = None, translation: str = "sub") -> int:
    if action in PROFILES or action == "all":
        import shutil
        from ..health.runner import check_provider
        from ..core.terminal import fit_terminal_line

        def update(stage, index, total, title):
            message = f"{stage} {index}/{total} — {title}"
            if sys.stderr.isatty():
                width = max(1, shutil.get_terminal_size(fallback=(80, 24)).columns - 1)
                message = fit_terminal_line(message, width + 1)
                print("\r" + message, end="\033[K", file=sys.stderr, flush=True)
            else:
                print(message, file=sys.stderr, flush=True)
        progress_terminal = sys.stderr.isatty()
        try:
            if progress_terminal:
                print("\033[?25l", end="", file=sys.stderr, flush=True)
            def collect(provider_id, **kwargs):
                name = PROFILES[provider_id]['name']
                return check_provider(provider_id, **kwargs,
                    progress=lambda stage, *args: update(f"{name} · {stage}", *args),
                    announce=lambda message: print(message, flush=True))
            if action == "all":
                from ..health.batch import check_batch
                from ..provider_manifest import disabled_providers_from_config, PROVIDER_REGISTRY
                disabled = disabled_providers_from_config()
                ids = [pid for pid in PROFILES if pid not in disabled and
                       PROVIDER_REGISTRY.get(pid, {}).get('status', 'active') == 'active']
                print(f"Checking {len(ids)} enabled providers sequentially; each keeps its supported verification depth. This can take several minutes per provider.", flush=True)
                data = check_batch(ids, run_provider=collect, seed=seed, verify=verify, translation=translation)
            else:
                data = collect(action, seed=seed, verify=verify, translation=translation)
        finally:
            if progress_terminal:
                print("\033[?25h", file=sys.stderr, flush=True)
        try:
            run_id = health_reports.save_health_report(health_reports.reports_directory(), data)
            print(f"Saved health report: {run_id}", flush=True)
            if data["status"] == "cancelled":
                print("Check cancelled; completed evidence was saved.")
                return 130
            return show_report(health_reports.read_saved_report(health_reports.reports_directory(), run_id), plain=plain)
        except (OSError, ValueError):
            print("Could not save or open the health report.", file=sys.stderr)
            return 1
    if action == "report":
        try:
            if delete:
                removed = health_reports.delete_saved_report(health_reports.reports_directory(), report_id or "latest")
                print(f"Deleted health report: {removed}")
                return 0
            text = health_reports.read_saved_report(health_reports.reports_directory(), report_id or "latest")
        except FileNotFoundError:
            message = "No saved health reports yet." if report_id == "latest" else "Health report not found. Use 'anyplay health reports' to list IDs."
            print(message, file=sys.stderr)
            return 1
        except (OSError, ValueError):
            if delete:
                print("Could not delete health report: invalid data, unexpected files, or unavailable file. Remaining files were kept.", file=sys.stderr)
            else:
                print("Could not open health report: missing readable text, invalid data, or unavailable file.", file=sys.stderr)
            return 1
        return show_report(text, plain=plain)
    if action != "reports":
        return 2
    try:
        rows, skipped = health_reports.list_saved_reports(health_reports.reports_directory())
    except OSError as error:
        print(f"Could not list health reports ({type(error).__name__}).", file=sys.stderr)
        return 1
    if not rows:
        print("No saved health reports yet.")
    else:
        print("Saved health reports (newest first; dates in local timezone)\n")
        width = max(2, max(len(row.run_id) for row in rows))
        print(f"{'ID':<{width}}  {'Checked':<29}  {'Run state':<10}  Verification")
        for row in rows:
            checked = row.started_at.astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
            print(f"{row.run_id:<{width}}  {checked:<29}  {row.status:<10}  {row.verification}")
    if skipped:
        noun = "report" if skipped == 1 else "reports"
        print(f"Skipped {skipped} unreadable or unsupported {noun}.")
    return 0
