"""Synthetic clearance only; never read a browser profile or use the network."""

import dataclasses
import json
import os
import sys
import time
from copy import copy
from threading import Event
from types import SimpleNamespace

import pytest

from media_cli.services.browser_access import clearance as module
from media_cli.services.browser_access.policy import BrowserPolicy

URL = "https://www.animepahe.pw/api/search"
POLICY = BrowserPolicy("animepahe", ["https://www.animepahe.pw", "https://other.test"])


def export():
    partition = {"topLevelSite": "https://animepahe.pw", "hasCrossSiteAncestor": False}
    return {"cookies": [{"name": "cf_clearance", "value": "synthetic-secret",
                         "domain": ".animepahe.pw", "path": "/", "secure": True,
                         "httpOnly": True, "session": False, "hostOnly": False,
                         "expirationDate": time.time() + 3600, "sameSite": "no_restriction",
                         "storeId": "0", "partitionKey": partition}],
            "user_agent": "Synthetic Chrome/132.0", "partition_key": partition}


def clearance():
    result = module.Clearance.from_export(POLICY, URL, export())
    assert result is not None
    return result


@pytest.mark.parametrize("mode", ["ok", "overflow", "cancel", "foreign-origin"])
def test_persistent_request_is_bounded_nonstreaming_and_does_not_close_owner(mode):
    from curl_cffi.curl import CURL_WRITEFUNC_ERROR

    from media_cli.services.browser_access.profiles import BrowserAccessCancelled

    item, cancel, calls = clearance(), Event(), []
    class Persistent:
        def close(self):
            pytest.fail("Borrowed session was closed")
        def get(self, url, **kwargs):
            calls.append(kwargs)
            assert not kwargs.get("stream")
            assert kwargs["allow_redirects"] is False
            assert list(self.cookies) == ["cf_clearance"]
            assert kwargs["headers"]["User-Agent"] == item.user_agent
            assert "Cookie" not in kwargs["headers"]
            if mode == "cancel":
                cancel.set()
            chunk = b"x" * (1048576 + 1) if mode == "overflow" else b"bounded"
            written = kwargs["content_callback"](chunk)
            assert written == (CURL_WRITEFUNC_ERROR if mode in {"overflow", "cancel"} else len(chunk))
            return SimpleNamespace(url="https://other.test/" if mode == "foreign-origin" else url,
                                   status_code=200, encoding="utf-8", headers={}, close=lambda: None)
    session = Persistent()
    if mode == "ok":
        result = module.request_with_clearance(URL, item, session=session, cancel=cancel,
                                                headers={"Cookie": "unrelated=secret", "User-Agent": "wrong"})
        assert result.body == "bounded" and result.clearance is not None
        assert result.clearance.cookies[0]["value"] == "synthetic-secret"
    else:
        error = BrowserAccessCancelled if mode == "cancel" else module.ClearanceRequestError
        with pytest.raises(error):
            module.request_with_clearance(URL, item, session=session, cancel=cancel)
    assert len(calls) == 1


@pytest.mark.parametrize("mode", ["binary", "overflow", "html-challenge"])
def test_binary_replay_retains_bytes_and_enforces_receive_limit(mode):
    from curl_cffi.curl import CURL_WRITEFUNC_ERROR

    data = (b"<title>Just a moment...</title><script src='/cdn-cgi/challenge-platform/a'></script>"
            if mode == "html-challenge" else b"RIFFxxxxWEBPbinary\xff\xfe")
    limit = len(data) - 1 if mode == "overflow" else len(data)
    class Session:
        def get(self, url, **kwargs):
            assert kwargs["allow_redirects"] is False
            written = kwargs["content_callback"](data)
            assert written == (CURL_WRITEFUNC_ERROR if mode == "overflow" else len(data))
            return SimpleNamespace(url=url, status_code=200, headers={}, encoding="utf-8", close=lambda: None)
    if mode == "overflow":
        with pytest.raises(module.ClearanceRequestError):
            module.request_with_clearance(URL, clearance(), session=Session(), binary=True, max_bytes=limit)
    else:
        result = module.request_with_clearance(URL, clearance(), session=Session(), binary=True, max_bytes=limit)
        assert result.data == data and "RIFF" not in repr(result)
        if mode == "html-challenge":
            from media_cli.services.browser_access.challenge import is_verification_challenge
            assert is_verification_challenge(result.status, result.headers, result.body)


@pytest.mark.parametrize("extra", [0, 1])
def test_binary_replay_eight_megabyte_boundary(extra):
    from curl_cffi.curl import CURL_WRITEFUNC_ERROR
    limit = 8 * 1024 * 1024
    data = b"x" * (limit + extra)
    class Session:
        def get(self, url, **kwargs):
            written = kwargs["content_callback"](data)
            assert written == (CURL_WRITEFUNC_ERROR if extra else limit)
            return SimpleNamespace(url=url, status_code=200, headers={}, encoding="utf-8", close=lambda: None)
    if extra:
        with pytest.raises(module.ClearanceRequestError):
            module.request_with_clearance(URL, clearance(), session=Session(), binary=True, max_bytes=limit)
    else:
        assert module.request_with_clearance(URL, clearance(), session=Session(), binary=True, max_bytes=limit).data == data


def test_clearance_is_immutable_scoped_and_secret_safe(monkeypatch):
    payload = export()
    item = module.Clearance.from_export(POLICY, URL, payload)
    assert item is not None and item.origin == "https://www.animepahe.pw"
    assert item.user_agent == "Synthetic Chrome/132.0"
    assert item.valid_for(POLICY, URL)
    assert not item.valid_for(BrowserPolicy("other", POLICY.origins), URL)
    assert not item.valid_for(POLICY, "https://other.test/")
    assert not item.valid_for(POLICY, "http://www.animepahe.pw/")
    assert "synthetic-secret" not in repr(item)
    assert "Synthetic Chrome" not in repr(item)
    payload["cookies"][0]["value"] = "changed"
    cookies = item.cookies
    cookies[0]["value"] = "changed-again"
    assert item.cookies[0]["value"] == "synthetic-secret"
    with pytest.raises(dataclasses.FrozenInstanceError):
        item.origin = "https://other.test"
    monkeypatch.setattr(module.time, "time", lambda: item.expires + 1)
    assert not item.valid_for(POLICY, URL)


@pytest.mark.parametrize("change", [
    lambda p: p.update(extra=True),
    lambda p: p.update(user_agent="UA\r\nInjected: yes"),
    lambda p: p.update(user_agent="x" * 2049),
    lambda p: p.update(cookies=[]),
    lambda p: p.update(cookies=p["cookies"] * 9),
    lambda p: p["cookies"][0].update(name="session"),
    lambda p: p["cookies"][0].update(value="bad;other=secret"),
    lambda p: p["cookies"][0].update(value="x" * 4097),
    lambda p: p["cookies"][0].update(domain=".evil.test"),
    lambda p: p["cookies"][0].update(domain=".pw"),
    lambda p: p["cookies"][0].update(domain=".co.uk"),
    lambda p: p["cookies"][0].update(path="/elsewhere"),
    lambda p: p["cookies"][0].update(path="//"),
    lambda p: p["cookies"][0].update(secure=False),
    lambda p: p["cookies"][0].update(expirationDate=0),
    lambda p: p["cookies"][0].update(expirationDate=float("inf")),
    lambda p: p["cookies"][0].update(expirationDate=True),
    lambda p: p["cookies"][0].update(session=True),
    lambda p: p.update(partition_key={"topLevelSite": "https://evil.test"}),
    lambda p: p.update(partition_key={"topLevelSite": "https://pw"}),
    lambda p: p.update(partition_key=None),
    lambda p: p["cookies"][0].update(partitionKey={"topLevelSite": "https://evil.test"}),
    lambda p: p["cookies"][0].update(partitionKey={"topLevelSite": "https://animepahe.pw", "hasCrossSiteAncestor": True}),
])
def test_invalid_exports_are_rejected(change):
    payload = export()
    change(payload)
    assert module.Clearance.from_export(POLICY, URL, payload) is None


def test_unpartitioned_cookie_and_path_boundary():
    payload = export()
    payload["partition_key"] = None
    payload["cookies"][0].pop("partitionKey")
    payload["cookies"][0]["path"] = "/api"
    item = module.Clearance.from_export(POLICY, URL, payload)
    assert item is not None
    assert not item.valid_for(POLICY, "https://www.animepahe.pw/apiculture")


def test_cache_missing_does_not_create_and_roundtrip_is_private(tmp_path):
    root = tmp_path / "missing" / "cache"
    store = module.ClearanceStore(root)
    assert store.load(POLICY, URL) is None
    assert not root.parent.exists()
    store.save(POLICY, URL, clearance())
    assert store.load(POLICY, URL).valid_for(POLICY, URL)
    files = list(root.rglob("*.json"))
    assert len(files) == 1
    assert all(path.stat().st_mode & 0o777 == 0o700 for path in [root, *root.iterdir()] if path.is_dir())
    assert files[0].stat().st_mode & 0o777 == 0o600
    assert "synthetic" not in str(files[0])
    assert store.load(BrowserPolicy("another", POLICY.origins), URL) is None
    assert store.load(POLICY, "https://other.test/") is None
    store.invalidate(POLICY, URL)
    assert store.load(POLICY, URL) is None


def test_cache_origin_files_are_separate_and_wrong_binding_cannot_save(tmp_path):
    store = module.ClearanceStore(tmp_path / "cache")
    store.save(POLICY, URL, clearance())
    payload = export()
    payload["cookies"][0]["domain"] = "other.test"
    payload["cookies"][0]["hostOnly"] = True
    payload["partition_key"] = None
    payload["cookies"][0].pop("partitionKey")
    other = module.Clearance.from_export(POLICY, "https://other.test/", payload)
    store.save(POLICY, "https://other.test/", other)
    assert len(list(store.root.rglob("*.json"))) == 2
    store.save(POLICY, "https://other.test/", clearance())
    assert store.load(POLICY, "https://other.test/").origin == "https://other.test"
    store.invalidate(POLICY, URL)
    assert store.load(POLICY, "https://other.test/") is not None


@pytest.mark.parametrize("damage", ["corrupt", "expired", "oversize", "mode", "symlink", "hardlink"])
def test_unsafe_or_invalid_cache_is_a_miss(tmp_path, damage):
    store = module.ClearanceStore(tmp_path / "cache")
    store.save(POLICY, URL, clearance())
    path = next(store.root.rglob("*.json"))
    if damage == "corrupt":
        path.write_text("{bad")
    elif damage == "expired":
        record = json.loads(path.read_text())
        record["payload"]["cookies"][0]["expirationDate"] = 1
        path.write_text(json.dumps(record))
    elif damage == "oversize":
        path.write_bytes(b"x" * 65537)
    elif damage == "mode":
        path.chmod(0o644)
    elif damage == "symlink":
        path.unlink()
        path.symlink_to(tmp_path / "outside")
    else:
        os.link(path, tmp_path / "outside")
    assert store.load(POLICY, URL) is None
    if damage in {"corrupt", "expired", "oversize"}:
        assert not path.exists()


def test_cache_rejects_symlink_ancestors_and_io_failure(tmp_path, monkeypatch):
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o700)
    alias = tmp_path / "alias"
    alias.symlink_to(outside, target_is_directory=True)
    store = module.ClearanceStore(alias / "cache")
    store.save(POLICY, URL, clearance())
    assert not list(outside.iterdir())
    assert store.load(POLICY, URL) is None
    store.invalidate(POLICY, URL)
    store = module.ClearanceStore(tmp_path / "cache")
    item = clearance()
    def denied(*args, **kwargs):
        raise PermissionError("denied")
    with monkeypatch.context() as patch:
        patch.setattr(module.os, "open", denied)
        assert store.load(POLICY, URL) is None
        store.save(POLICY, URL, item)
        store.invalidate(POLICY, URL)


def test_http_replay_preserves_cookie_scope_and_response(monkeypatch):
    seen = {}
    class Session:
        def __init__(self, **kwargs):
            seen.update(kwargs)
        def get(self, url, **kwargs):
            seen.update(kwargs)
            seen["url"] = url
            return SimpleNamespace(url=url, status_code=403, headers={"cf-mitigated": "challenge"},
                                   encoding="utf-8", iter_content=lambda: iter([b"challenge"]), close=lambda: None)
        def close(self):
            seen["closed"] = True
    monkeypatch.setitem(sys.modules, "curl_cffi.requests", SimpleNamespace(Session=Session))
    headers = {"Cookie": "caller=secret", "USER-AGENT": "wrong", "Accept": "text/html"}
    result = module.request_with_clearance(URL, clearance(), headers=headers, timeout=7)
    assert result.status == 403 and result.body == "challenge"
    assert result.headers["cf-mitigated"] == "challenge"
    assert seen["impersonate"] == "chrome" and seen["allow_redirects"] is False
    assert seen["headers"] == {"Accept": "text/html", "User-Agent": "Synthetic Chrome/132.0"}
    cookie = list(seen["cookies"])[0]
    assert (cookie.name, cookie.domain, cookie.path, cookie.secure) == ("cf_clearance", ".animepahe.pw", "/", True)
    assert cookie.expires > time.time()
    assert seen["timeout"] == 7 and seen["closed"]
    assert headers["Cookie"] == "caller=secret"


def test_replay_fails_closed_without_dependency_or_on_error(monkeypatch):
    item = clearance()
    monkeypatch.setitem(sys.modules, "curl_cffi.requests", None)
    with pytest.raises(module.ClearanceRequestError):
        module.request_with_clearance(URL, item)
    with pytest.raises(module.ClearanceRequestError):
        module.request_with_clearance("https://other.test/", item)
    class Session:
        def __init__(self, **kwargs):
            raise RuntimeError("synthetic-secret")
    monkeypatch.setitem(sys.modules, "curl_cffi.requests", SimpleNamespace(Session=Session))
    with pytest.raises(module.ClearanceRequestError) as exc:
        module.request_with_clearance(URL, item)
    assert "synthetic-secret" not in str(exc.value)


@pytest.mark.parametrize("headers", [{"Accept": "x\r\nCookie: leak"}, {"Host": "evil.test"}])
def test_replay_rejects_unsafe_headers_without_network(headers):
    with pytest.raises(module.ClearanceRequestError):
        module.request_with_clearance(URL, clearance(), headers=headers)


@pytest.mark.parametrize("kind", ["over-limit", "decoded-limit", "redirect", "network", "exact-limit"])
def test_stream_limit_origin_failure_and_cleanup(monkeypatch, kind):
    closed = []
    def chunks():
        if kind == "network":
            raise OSError("synthetic-secret")
        yield b"x" * 1048576 if kind != "decoded-limit" else b"\xff" * 524288
        if kind == "over-limit":
            yield b"x"
    class Session:
        def __init__(self, **kwargs):
            pass
        def get(self, url, **kwargs):
            assert kwargs["allow_redirects"] is False
            return SimpleNamespace(url="https://other.test/" if kind == "redirect" else url,
                                   status_code=200, headers={}, encoding="utf-8", iter_content=chunks,
                                   close=lambda: closed.append("response"))
        def close(self):
            closed.append("session")
    monkeypatch.setitem(sys.modules, "curl_cffi.requests", SimpleNamespace(Session=Session))
    if kind == "exact-limit":
        assert len(module.request_with_clearance(URL, clearance()).body) == 1048576
    else:
        with pytest.raises(OSError, match="^Clearance HTTP request failed$"):
            module.request_with_clearance(URL, clearance())
    assert closed == ["response", "session"]


@pytest.mark.parametrize("kind", ["root-mode", "provider-mode", "foreign-uid", "provider-link"])
def test_unsafe_directories_are_never_used(tmp_path, monkeypatch, kind):
    store = module.ClearanceStore(tmp_path / "cache")
    item = clearance()
    store.save(POLICY, URL, item)
    original = next(store.root.rglob("*.json"))
    content = original.read_bytes()
    if kind == "root-mode":
        store.root.chmod(0o755)
    elif kind == "provider-mode":
        original.parent.chmod(0o755)
    elif kind == "provider-link":
        moved = tmp_path / "outside"
        original.parent.rename(moved)
        original.parent.symlink_to(moved, target_is_directory=True)
    with monkeypatch.context() as patch:
        if kind == "foreign-uid":
            uid = os.getuid()
            patch.setattr(module.os, "getuid", lambda: uid + 1)
        assert store.load(POLICY, URL) is None
        store.save(POLICY, URL, item)
        store.invalidate(POLICY, URL)
    assert original.read_bytes() == content


def test_failed_atomic_publication_preserves_previous_cache(tmp_path, monkeypatch):
    store = module.ClearanceStore(tmp_path / "cache")
    store.save(POLICY, URL, clearance())
    original = next(store.root.rglob("*.json"))
    before = original.read_bytes()
    payload = export()
    payload["cookies"][0]["value"] = "replacement-secret"
    replacement = module.Clearance.from_export(POLICY, URL, payload)
    def fail(*args, **kwargs):
        raise OSError("cannot publish")
    monkeypatch.setattr(module.os, "replace", fail)
    store.save(POLICY, URL, replacement)
    assert original.read_bytes() == before
    assert list(original.parent.iterdir()) == [original]


def test_public_suffix_and_cross_site_partition_rejected_for_matching_suffix_host():
    policy = BrowserPolicy("test", ["https://media.example.co.uk"])
    payload = export()
    payload["cookies"][0]["domain"] = ".co.uk"
    payload["partition_key"] = None
    payload["cookies"][0].pop("partitionKey")
    assert module.Clearance.from_export(policy, "https://media.example.co.uk/", payload) is None
    payload["cookies"][0]["domain"] = ".example.co.uk"
    assert module.Clearance.from_export(policy, "https://media.example.co.uk/", payload) is not None
    partition = {"topLevelSite": "https://other.co.uk"}
    payload["partition_key"] = partition
    payload["cookies"][0]["partitionKey"] = partition
    assert module.Clearance.from_export(policy, "https://media.example.co.uk/", payload) is None


def test_cancelled_save_does_not_create_root(tmp_path):
    store = module.ClearanceStore(tmp_path / "missing" / "cache")
    cancel = Event()
    cancel.set()
    store.save(POLICY, URL, clearance(), cancel=cancel)
    assert not store.root.parent.exists()


@pytest.mark.parametrize("phase", ["file-fsync", "replace", "directory-fsync"])
def test_cancel_during_save_leaves_no_cookie_or_temporary_file(tmp_path, monkeypatch, phase):
    store = module.ClearanceStore(tmp_path / "cache")
    cancel = Event()
    original_fsync, original_replace = module.os.fsync, module.os.replace
    def fsync(fd):
        original_fsync(fd)
        is_directory = os.path.isdir(f"/proc/self/fd/{fd}")
        if (phase == "directory-fsync" and is_directory) or (phase == "file-fsync" and not is_directory):
            cancel.set()
    def replace(*args, **kwargs):
        original_replace(*args, **kwargs)
        if phase == "replace":
            cancel.set()
    monkeypatch.setattr(module.os, "fsync", fsync)
    monkeypatch.setattr(module.os, "replace", replace)
    store.save(POLICY, URL, clearance(), cancel=cancel)
    assert cancel.is_set()
    assert store.load(POLICY, URL) is None
    assert not [path for path in store.root.rglob("*") if path.is_file()]


def test_cancel_before_publication_keeps_previous_cache(tmp_path, monkeypatch):
    store = module.ClearanceStore(tmp_path / "cache")
    item = clearance()
    store.save(POLICY, URL, item)
    original = next(store.root.rglob("*.json"))
    before = original.read_bytes()
    cancel = Event()
    original_fsync = module.os.fsync
    def fsync(fd):
        original_fsync(fd)
        cancel.set()
    monkeypatch.setattr(module.os, "fsync", fsync)
    store.save(POLICY, URL, item, cancel=cancel)
    assert original.read_bytes() == before
    assert list(original.parent.iterdir()) == [original]


@pytest.mark.parametrize("rotation", ["valid", "expired", "insecure", "foreign-domain", "removed", "unchanged"])
def test_replay_returns_only_valid_clearance_rotation(monkeypatch, rotation):
    item = clearance()
    expiry = int(time.time() + 7200)
    class Session:
        def __init__(self, **kwargs):
            self.cookies = SimpleNamespace(jar=kwargs["cookies"])
        def get(self, url, **kwargs):
            jar = self.cookies.jar
            original = next(iter(jar))
            assert (original.domain, original.path, original.secure) == (".animepahe.pw", "/", True)
            if rotation != "unchanged":
                jar.clear()
                if rotation != "removed":
                    rotated = copy(original)
                    rotated.value = "synthetic-rotated-secret"
                    rotated.expires = 1 if rotation == "expired" else expiry
                    rotated.secure = rotation != "insecure"
                    rotated.domain = ".evil.test" if rotation == "foreign-domain" else original.domain
                    jar.set_cookie(rotated)
            unrelated = copy(original)
            unrelated.name = "unrelated-session"
            unrelated.value = "unrelated-secret"
            jar.set_cookie(unrelated)
            return SimpleNamespace(url=url, status_code=200, headers={}, encoding="utf-8",
                                   iter_content=lambda: iter([b"ok"]), close=lambda: None)
        def close(self):
            pass
    monkeypatch.setitem(sys.modules, "curl_cffi.requests", SimpleNamespace(Session=Session))
    response = module.request_with_clearance(URL, item)
    assert response.status == 200 and response.body == "ok"
    if rotation in {"valid", "unchanged"}:
        updated = response.clearance
        assert updated is not None and updated.valid_for(POLICY, URL)
        assert len(updated.cookies) == 1
        cookie = updated.cookies[0]
        assert cookie["name"] == "cf_clearance"
        assert (cookie["domain"], cookie["path"], cookie["secure"]) == (".animepahe.pw", "/", True)
        assert updated.partition_key == item.partition_key
        assert cookie["partitionKey"] == item.partition_key
        assert updated.user_agent == item.user_agent
        if rotation == "valid":
            assert cookie["value"] == "synthetic-rotated-secret"
            assert updated.expires == expiry
        else:
            assert cookie["value"] == "synthetic-secret"
        assert "unrelated-secret" not in json.dumps(list(updated.cookies))
        assert "synthetic-rotated-secret" not in repr(response)
    else:
        assert response.clearance is None
    assert item.cookies[0]["value"] == "synthetic-secret"


def test_replay_missing_session_jar_preserves_original_snapshot(monkeypatch):
    item = clearance()
    class Session:
        def __init__(self, **kwargs):
            pass
        def get(self, url, **kwargs):
            return SimpleNamespace(url=url, status_code=200, headers={}, encoding="utf-8",
                                   iter_content=lambda: iter([b"ok"]), close=lambda: None)
        def close(self):
            pass
    monkeypatch.setitem(sys.modules, "curl_cffi.requests", SimpleNamespace(Session=Session))
    assert module.request_with_clearance(URL, item).clearance is item
