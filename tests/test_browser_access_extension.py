"""Private browser-helper packaging and the real JavaScript executor contract."""

import json
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from media_cli.services.browser_access.extension import packaged_extension
from media_cli.services.browser_access.policy import BrowserPolicy, BrowserPolicyError
from media_cli.services.browser_access.profiles import BrowserProfiles


def test_packaged_extension_is_private_scoped_and_removed(tmp_path):
    config = {"endpoint": "http://127.0.0.1:12345", "token": "a" * 43}
    policy = BrowserPolicy("animexin", ["https://animexin.dev"])
    with BrowserProfiles(tmp_path / "browser").lease("animexin") as profile:
        with packaged_extension(profile, policy, config, "https://animexin.dev/") as path:
            assert stat.S_IMODE(path.stat().st_mode) == 0o700
            assert all(stat.S_IMODE(file.stat().st_mode) == 0o600 for file in path.iterdir())
            manifest = json.loads((path / "manifest.json").read_text())
            assert manifest["host_permissions"] == ["https://animexin.dev/*", "http://127.0.0.1/*"]
            assert "web_accessible_resources" not in manifest
            assert "cookies" in manifest["permissions"]
            assert manifest["minimum_chrome_version"] == "132"
            text = (path / "config.js").read_text()
            assert config["token"] in text
            assert config["token"] not in (path / "worker.js").read_text()
        assert not path.exists()
        assert profile.path.exists()


def test_mismatched_provider_cannot_package_extension(tmp_path):
    with BrowserProfiles(tmp_path / "browser").lease("one") as profile:
        with pytest.raises(BrowserPolicyError):
            with packaged_extension(profile, BrowserPolicy("two", ["https://two.test"]),
                                    {"endpoint": "http://127.0.0.1:12345", "token": "a" * 43},
                                    "https://two.test/"):
                pytest.fail("provider mismatch accepted")
        assert list(profile.path.iterdir()) == []


@pytest.mark.parametrize("endpoint", [
    "http://evil.test:12345", "http://localhost:12345", "https://127.0.0.1:12345",
    "http://127.0.0.1:12345/path", "http://127.0.0.1:12345?secret=x",
])
def test_bad_broker_endpoint_rejected_before_writing(tmp_path, endpoint):
    with BrowserProfiles(tmp_path / "browser").lease("test") as profile:
        with pytest.raises(BrowserPolicyError):
            with packaged_extension(profile, BrowserPolicy("test", ["https://site.test"]),
                                    {"endpoint": endpoint, "token": "a" * 43}, "https://site.test/"):
                pytest.fail("bad endpoint accepted")
        assert list(profile.path.iterdir()) == []


def test_extension_cleanup_on_failure_does_not_delete_browser_state(tmp_path):
    with BrowserProfiles(tmp_path / "browser").lease("test") as profile:
        marker = profile.path / "Cookies"
        marker.write_bytes(b"keep")
        with pytest.raises(RuntimeError):
            with packaged_extension(profile, BrowserPolicy("test", ["https://site.test"]),
                                    {"endpoint": "http://127.0.0.1:12345", "token": "a" * 43},
                                    "https://site.test/") as path:
                raise RuntimeError("cancelled")
        assert not path.exists()
        assert marker.read_bytes() == b"keep"


def test_actual_packaged_javascript_executor():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for the browser helper JavaScript tests")
    harness = Path(__file__).with_name("browser_access_extension_harness.cjs")
    result = subprocess.run([node, str(harness)], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "executor checks passed" in result.stdout
