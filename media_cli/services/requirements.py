"""Load optional/required Python dependencies only at their use boundaries."""

import importlib
from types import ModuleType

from ..constants import APP_NAME
from ..core.api import ProviderDependencyError
from ..core.terminal import sanitize_terminal_text

_PACKAGES = {
    "bs4": "beautifulsoup4", "curl_cffi": "curl-cffi", "m3u8": "m3u8",
    "Crypto": "pycryptodome", "Cryptodome": "pycryptodomex", "cryptography": "cryptography",
}
_OPERATIONS = {"episodes": "episode list", "sources": "playback", "title": "title lookup",
               "mirrors": "quality selection", "remote_sources": "stream extraction"}


def _package(module: str) -> str:
    root = module.split(".", 1)[0]
    return _PACKAGES.get(root, root)


def optional_python(module: str) -> ModuleType | None:
    try:
        return importlib.import_module(module)
    except (ImportError, OSError):
        return None


def optional_curl_requests() -> ModuleType | None:
    return optional_python("curl_cffi.requests")


def optional_dependency_hint(module: str, *, fallback: str, feature: str, playback_may_fail: bool = False) -> str:
    """Describe a lost capability; opt into uncertainty only for a degraded route."""
    state = f"{fallback} may fail" if playback_may_fail else fallback
    return sanitize_terminal_text(f"{state}; install {_package(module)} for {feature}.")


def dependency_hint_messages(payload: dict | None) -> tuple[str, ...]:
    """Bound and sanitize cached/provider messages before a terminal consumes them."""
    if not isinstance(payload, dict):
        return ()
    raw = payload.get("dependency_hints", ())
    if not isinstance(raw, (list, tuple)):
        return ()
    messages = []
    for value in raw[:8]:
        if isinstance(value, str):
            message = sanitize_terminal_text(value[:1024])[:240]
            if message and message not in messages:
                messages.append(message)
    return tuple(messages)


class MissingPythonDependency(ProviderDependencyError):
    """A recoverable source-local failure, with safe install/advisory metadata."""

    def __init__(self, modules, context: str):
        self.modules = tuple(modules)
        packages = tuple(dict.fromkeys(_package(module) for module in self.modules))
        needed = packages[0] if len(packages) == 1 else "one of: " + ", ".join(packages)
        hints = "\n".join(f"  pipx inject {APP_NAME} {package}" for package in packages)
        super().__init__(
            f"{context} requires {needed}. The required library is unavailable or could not be loaded.\n"
            f"Install {'one' if len(packages) > 1 else 'it'} in Anyplay's environment:\n{hints}\n"
            f"For a pip installation: pip install {packages[0]}\n"
            "Then retry, or choose another provider."
        )


def dependency_failure_hint(error: ProviderDependencyError, *, fallback=False) -> str:
    if isinstance(error, MissingPythonDependency):
        return optional_dependency_hint(
            error.modules[0], fallback="Fallback playback" if fallback else "Some sources unavailable",
            feature="native extraction", playback_may_fail=fallback,
        )
    return sanitize_terminal_text(str(error).splitlines()[0])[:240]


def add_dependency_hints(payload: dict, messages) -> dict:
    """Copy feedback into a result without modifying provider/cache payloads."""
    result = dict(payload)
    hints = dependency_hint_messages({"dependency_hints": [*dependency_hint_messages(payload), *messages]})
    if hints:
        result["dependency_hints"] = list(hints)
    return result


def require_any_python(modules, context: str) -> ModuleType:
    modules = tuple(modules)
    if not modules:
        raise ValueError("A dependency must have at least one backend")
    for module in modules:
        loaded = optional_python(module)
        if loaded is not None:
            return loaded
    raise MissingPythonDependency(modules, context) from None


def require_python(module: str, context: str) -> ModuleType:
    return require_any_python((module,), context)


class MissingExecutable(ProviderDependencyError):
    """An action needs a system tool, not another Python package."""

    def __init__(self, program: str, context: str):
        self.program = program
        package = {"node": "Node.js", "bwrap": "bubblewrap", "prlimit": "util-linux"}.get(program, program)
        install = "Install it with: pkg install termux-am." if program == "am" else (
            f"Install {package} using your system package manager."
        )
        super().__init__(sanitize_terminal_text(
            f"{context} requires '{program}', which was not found on PATH.\n"
            f"{install}\nThen retry this action."
        ))


def require_executable(program: str, context: str) -> str:
    import shutil

    executable = shutil.which(program)
    if executable is None:
        raise MissingExecutable(program, context)
    return executable


def require_provider_operation(provider_id: str, operation: str, *, metadata=None) -> None:
    from ..provider_manifest import _PROVIDER_ALIASES, PROVIDER_REGISTRY

    key = _PROVIDER_ALIASES.get(str(provider_id).casefold(), str(provider_id).casefold())
    metadata = PROVIDER_REGISTRY.get(key, {}) if metadata is None else metadata
    requirements = metadata.get("python_requirements", {})
    needed = list(requirements.get("import", ()))
    if operation != "import":
        needed.extend(requirements.get("all", ()))
        needed.extend(requirements.get(operation, ()))
    label = metadata.get("name", key)
    context = f"{label} {_OPERATIONS.get(operation, operation)}" if operation != "import" else label
    for requirement in needed:
        if isinstance(requirement, str):
            require_python(requirement, context)
        else:
            require_any_python(requirement, context)
