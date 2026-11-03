"""Classify diagnostic prerequisites without blaming remote providers."""


def classify_failure(error: Exception) -> dict:
    from ..core.api import ProviderDependencyError
    from ..services.requirements import MissingExecutable, MissingPythonDependency
    from ..services.android_webview.client import CompanionSetupRequired, CompanionUnavailable

    if isinstance(error, (CompanionSetupRequired, CompanionUnavailable)):
        kind = "companion"
    elif isinstance(error, (MissingPythonDependency, ImportError)):
        kind = "python"
    elif isinstance(error, (MissingExecutable, FileNotFoundError)):
        kind = "executable"
    elif isinstance(error, ProviderDependencyError):
        kind = "runtime"
    else:
        return {"status": "error", "issue_scope": "unknown", "reason": type(error).__name__}
    return {"status": "missing_dependency", "issue_scope": "local",
            "requirement_kind": kind, "reason": str(error)}


def record_local_requirement(report: dict, provider: dict, stage: dict, outcome: dict) -> bool:
    """Keep provider health unknown when a local requirement prevents a check."""
    if outcome.get("status") != "missing_dependency" and outcome.get("issue_scope") != "local":
        return False
    kind = outcome.get("requirement_kind") or "dependency"
    if kind not in ("python", "executable", "runtime", "companion", "dependency"):
        kind = "dependency"
    reason = outcome.get("reason") or "A local requirement is unavailable"
    stage.update(status="not_verified", issue_scope="local", reason=reason)
    provider["status"] = "inconclusive"
    provider.setdefault("local_issues", []).append({"stage": stage["name"], "kind": kind, "reason": reason})
    report["status"] = "incomplete"
    return True
