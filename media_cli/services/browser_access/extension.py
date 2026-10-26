"""Package per-run extension credentials only inside an owned profile lease."""

from __future__ import annotations

import json
import os
import re
import stat
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from importlib.resources import files
from pathlib import Path

from .policy import BrowserPolicy, BrowserPolicyError
from .profiles import BrowserProfile, BrowserProfileError


@contextmanager
def packaged_extension(profile: BrowserProfile, policy: BrowserPolicy,
                       bridge_config: dict[str, str], startup_url: str) -> Iterator[Path]:
    """Caller must hold the profile lease until browser exit and this cleanup."""
    if profile.provider_id != policy.provider_id:
        raise BrowserPolicyError("Browser profile and policy providers differ")
    policy.validate_url(startup_url)
    endpoint, token = bridge_config.get("endpoint", ""), bridge_config.get("token", "")
    match = re.fullmatch(r"http://127\.0\.0\.1:([0-9]{1,5})", endpoint)
    if (not match or not 1 <= int(match[1]) <= 65535
            or not re.fullmatch(r"[A-Za-z0-9_-]{43}", token)
            or set(bridge_config) != {"endpoint", "token"}):
        raise BrowserPolicyError("Invalid private browser bridge configuration")
    info = profile.path.lstat()
    if (not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o700
            or info.st_uid != os.getuid() or profile.path.resolve() != profile.path):
        raise BrowserProfileError("Extension requires a private owned browser profile")
    origins = sorted(policy.origins)
    manifest = {
        "manifest_version": 3, "name": "Anyplay private browser helper", "version": "0.1.0",
        "minimum_chrome_version": "132",
        "permissions": ["scripting", "tabs", "storage", "alarms", "background", "cookies"],
        "host_permissions": [origin + "/*" for origin in origins] + ["http://127.0.0.1/*"],
        "background": {"service_worker": "worker.js"},
    }
    config = {**bridge_config, "origins": origins, "max_body_bytes": policy.max_body_bytes,
              "startup_url": startup_url, "create_window": True}
    assets = files("media_cli.services.browser_access").joinpath("assets")
    contents = {
        "manifest.json": json.dumps(manifest),
        "config.js": "const ANYPLAY_CONFIG = " + json.dumps(config, ensure_ascii=True) + ";\n",
        "executor.js": assets.joinpath("executor.js").read_text(encoding="utf-8"),
        "worker.js": assets.joinpath("worker.js").read_text(encoding="utf-8"),
    }
    with tempfile.TemporaryDirectory(prefix="bridge-", dir=profile.path) as folder:
        path = Path(folder)
        for name, content in contents.items():
            fd = os.open(path / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(content)
        yield path
