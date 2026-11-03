"""Distribution metadata, runtime assets and isolated utility entry points."""

import os
import shutil
import subprocess
import sys
import tarfile
import zipfile
from email.parser import Parser
from pathlib import Path

import pytest
from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib

ROOT = Path(__file__).resolve().parents[1]
PROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
ESSENTIALS = {"requests", "curl-cffi", "beautifulsoup4", "m3u8", "pycryptodome"}


def requirements(values):
    return {canonicalize_name(requirement.name): requirement for value in values
            for requirement in [Requirement(value)]}


def test_default_install_keeps_essential_provider_and_quality_modules():
    defaults = requirements(PROJECT["dependencies"])
    assert set(defaults) == ESSENTIALS
    assert "0.7.4" in defaults["curl-cffi"].specifier
    assert "0.7.3" not in defaults["curl-cffi"].specifier
    assert "6.0" in defaults["m3u8"].specifier and "7.0" not in defaults["m3u8"].specifier
    assert "3.19.0" in defaults["pycryptodome"].specifier
    assert "3.18.0" not in defaults["pycryptodome"].specifier
    assert all(requirement.marker is None for requirement in defaults.values())


def test_optional_features_have_clear_groups_and_compatible_aliases():
    extras = PROJECT["optional-dependencies"]
    assert extras["native-crypto"] == extras["allanime"]
    assert extras["legacy-browser"] == extras["animepahe-browser"]
    assert set(requirements(extras["native-crypto"])) == {"cryptography"}
    assert set(requirements(extras["legacy-browser"])) == {"playwright"}
    assert {"allanime-termux", "crypto-termux", "miruro", "dev"} <= extras.keys()
    assert {"setuptools", "wheel"} <= requirements(extras["dev"]).keys()


@pytest.fixture(scope="module")
def source_tree(tmp_path_factory):
    """Build from a snapshot so verification never shares checkout staging files."""
    source = tmp_path_factory.mktemp("package-inputs")
    shutil.copytree(ROOT / "media_cli", source / "media_cli", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for name in ("pyproject.toml", "README.md", "MANIFEST.in"):
        shutil.copy2(ROOT / name, source / name)
    (source / "docs").mkdir()
    shutil.copy2(ROOT / "docs/installation-dependencies.md", source / "docs/installation-dependencies.md")
    # Synthetic private files ensure the manifest excludes notes, rather than
    # passing only because the build snapshot happened to lack private docs.
    (source / "docs/TASKS.md").write_text("Private packaging canary.\n")
    (source / "docs/private-notes.md").write_text("Private packaging canary.\n")
    return source


@pytest.fixture(scope="module")
def wheel(tmp_path_factory, source_tree):
    output = tmp_path_factory.mktemp("wheel")
    result = subprocess.run(
        [sys.executable, "-c", "from setuptools import build_meta; import sys; build_meta.build_wheel(sys.argv[1])", str(output)],
        cwd=source_tree, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    paths = list(output.glob("*.whl"))
    assert len(paths) == 1
    return paths[0]


def test_wheel_contains_exact_runtime_assets_and_entry_point(wheel):
    assets = ["media_cli/providers/registry.json", "media_cli/extractors/senshi_runtime.cjs",
              "media_cli/services/browser_access/assets/executor.js", "media_cli/services/browser_access/assets/worker.js"]
    with zipfile.ZipFile(wheel) as archive:
        for asset in assets:
            assert archive.read(asset) == (ROOT / asset).read_bytes()
        assert not any(name.startswith(("tests/", "scratch/", ".git/", ".venv/")) for name in archive.namelist())
        entry_points = next(name for name in archive.namelist() if name.endswith(".dist-info/entry_points.txt"))
        assert "anyplay = media_cli.cli.main:run" in archive.read(entry_points).decode()


def test_wheel_metadata_installs_essentials_without_extras(wheel):
    with zipfile.ZipFile(wheel) as archive:
        metadata_name = next(name for name in archive.namelist() if name.endswith(".dist-info/METADATA"))
        metadata = Parser().parsestr(archive.read(metadata_name).decode())
    entries = [Requirement(value) for value in metadata.get_all("Requires-Dist", [])]
    defaults = {canonicalize_name(entry.name) for entry in entries if entry.marker is None}
    assert defaults == ESSENTIALS
    assert {str(entry) for entry in entries if entry.marker is None} == {str(Requirement(value)) for value in PROJECT["dependencies"]}
    assert {"native-crypto", "legacy-browser", *PROJECT["optional-dependencies"]} <= set(metadata.get_all("Provides-Extra", []))


@pytest.mark.parametrize("arguments", [["--help"], ["--version"], ["companion", "--help"], ["completion", "bash"]])
def test_installed_wheel_utilities_are_independent_of_optional_imports(wheel, tmp_path, arguments):
    with zipfile.ZipFile(wheel) as archive:
        archive.extractall(tmp_path)
    code = """
import importlib.abc, sys
class Guard(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        roots=('requests','curl_cffi','bs4','m3u8','Crypto','Cryptodome','cryptography','playwright',
               'media_cli.app_core','media_cli.providers','media_cli.playback','media_cli.extractors')
        if any(fullname==root or fullname.startswith(root+'.') for root in roots):
            raise AssertionError('Unrelated import: '+fullname)
sys.meta_path.insert(0,Guard())
sys.argv=['anyplay', *ARGS]
from media_cli.cli.main import run
raise SystemExit(run())
""".replace("ARGS", repr(arguments))
    result = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, capture_output=True, text=True,
                            env={**os.environ, "HOME": str(tmp_path), "PYTHONPATH": str(tmp_path), "PATH": ""}, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (tmp_path / ".local/state/anyplay").exists()


@pytest.mark.parametrize("extra", list(PROJECT["optional-dependencies"]))
def test_wheel_extras_resolve_to_the_declared_dependencies(wheel, extra):
    environment = {**default_environment(), "extra": extra}
    with zipfile.ZipFile(wheel) as archive:
        name = next(name for name in archive.namelist() if name.endswith(".dist-info/METADATA"))
        metadata = Parser().parsestr(archive.read(name).decode())

    def enabled(values):
        entries = [Requirement(value) for value in values]
        return {(canonicalize_name(entry.name), str(entry.specifier)) for entry in entries
                if entry.marker is None or entry.marker.evaluate(environment)}

    expected = [*PROJECT["dependencies"], *PROJECT["optional-dependencies"][extra]]
    assert enabled(metadata.get_all("Requires-Dist", [])) == enabled(expected)


def test_source_distribution_contains_public_reference_and_rebuilds(wheel, source_tree, tmp_path):
    output = tmp_path / "source"
    output.mkdir()
    result = subprocess.run(
        [sys.executable, "-c", "from setuptools import build_meta; import sys; build_meta.build_sdist(sys.argv[1])", str(output)],
        cwd=source_tree, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    archive_path = next(output.glob("*.tar.gz"))
    unpacked = tmp_path / "unpacked"
    unpacked.mkdir()
    with tarfile.open(archive_path) as archive:
        names = archive.getnames()
        reference = next(name for name in names if name.endswith("/docs/installation-dependencies.md"))
        assert archive.extractfile(reference).read() == (ROOT / "docs/installation-dependencies.md").read_bytes()
        assert {name for name in names if "/docs/" in name} == {reference}
        assert not any(name.endswith("/docs/TASKS.md") or "/scratch/" in name or "/.venv/" in name for name in names)
        assert all(not Path(member.name).is_absolute() and ".." not in Path(member.name).parts
                   and not member.issym() and not member.islnk() for member in archive.getmembers())
        archive.extractall(unpacked)
    source = next(unpacked.iterdir())
    rebuilt = tmp_path / "rebuilt"
    rebuilt.mkdir()
    result = subprocess.run(
        [sys.executable, "-c", "from setuptools import build_meta; import sys; build_meta.build_wheel(sys.argv[1])", str(rebuilt)],
        cwd=source, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    with zipfile.ZipFile(wheel) as original, zipfile.ZipFile(next(rebuilt.glob("*.whl"))) as rebuilt_archive:
        for name in original.namelist():
            if name.endswith(("/METADATA", "/entry_points.txt", ".json", ".js", ".cjs")):
                assert original.read(name) == rebuilt_archive.read(name), name
