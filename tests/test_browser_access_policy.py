"""Provider boundaries for the future browser-session executor."""

import pytest

from media_cli.services.browser_access.policy import BrowserPolicy, BrowserPolicyError


@pytest.fixture
def policy():
    return BrowserPolicy("animepahe", ("https://animepahe.pw", "https://kwik.si"))


def test_supported_commands_have_no_script_or_header_escape_hatch(policy):
    assert policy.command("fetch", "https://animepahe.pw/api?m=search&q=naruto") == {
        "operation": "fetch", "url": "https://animepahe.pw/api?m=search&q=naruto",
        "method": "GET", "redirect": "error", "max_body_bytes": 1048576,
    }
    assert policy.command("document", "https://kwik.si/e/123")["operation"] == "document"


@pytest.mark.parametrize("url", [
    "http://animepahe.pw/", "https://animepahe.pw.evil.test/",
    "https://evil.animepahe.pw/", "https://senshi.live/", "https://animepahe.pw:444/",
    "https://user:pass@animepahe.pw/", "https://animepahe.pw/#token",
    "https://animepahe.pw/\napi", " https://animepahe.pw/", "https://animepahe.pw\\@evil.test/",
    "file:///etc/passwd", "javascript:alert(1)", "https://127.0.0.1/", "//animepahe.pw/api",
    "https://animepahe.pw./", "https://animepahe.pw/%0afoo", "https://animepahe.pw/?x=%5cfoo",
])
def test_rejects_unapproved_or_ambiguous_urls(policy, url):
    with pytest.raises(BrowserPolicyError):
        policy.command("fetch", url)


@pytest.mark.parametrize("operation", ["evaluate", "cookies", "POST", "", None])
def test_operations_are_closed(policy, operation):
    with pytest.raises(BrowserPolicyError):
        policy.command(operation, "https://animepahe.pw/")


@pytest.mark.parametrize("origin", [
    "https://localhost", "https://127.0.0.1", "https://[::1]", "https://internal",
    "https://site.local", "https://site.localhost", "https://site.test/path",
    "https://site.test?x=1", "https://*.site.test", "https://site.test:444", "http://site.test",
])
def test_invalid_allowlist_origin_is_rejected(origin):
    with pytest.raises(BrowserPolicyError):
        BrowserPolicy("test", [origin])


def test_allowlist_is_a_snapshot_and_provider_bound():
    origins = ["https://site.test"]
    policy = BrowserPolicy("one", origins)
    origins.append("https://other.test")
    with pytest.raises(BrowserPolicyError):
        policy.command("fetch", "https://other.test/")
    assert policy.provider_id == "one"
    with pytest.raises(AttributeError):
        policy.provider_id = "other"


def test_reply_accepts_text_and_exact_origin(policy):
    reply = {"url": "https://animepahe.pw/api", "status": 200, "body": '{"data":[]}'}
    assert policy.response(reply) == reply


def test_encoded_spaces_in_search_queries_are_valid(policy):
    url = "https://animepahe.pw/api?m=search&q=oshi%20no%20ko"
    assert policy.command("fetch", url)["url"] == url


@pytest.mark.parametrize("changes", [
    {"url": "https://evil.test/"}, {"status": True}, {"status": 99}, {"status": 600},
    {"body": []}, {"body": "x" * 1048577}, {"body": "\ud800"}, {"cookies": "secret"},
])
def test_invalid_reply_is_rejected(policy, changes):
    reply = {"url": "https://animepahe.pw/", "status": 200, "body": "ok", **changes}
    with pytest.raises(BrowserPolicyError):
        policy.response(reply)


def test_size_bound_counts_utf8_bytes():
    policy = BrowserPolicy("test", ["https://site.test"], max_body_bytes=64)
    with pytest.raises(BrowserPolicyError):
        policy.response({"url": "https://site.test/", "status": 200, "body": "\u00e9" * 33})


@pytest.mark.parametrize("provider,origins,limit", [
    ("../bad", ["https://site.test"], 64), ("test", [], 64),
    ("test", ["https://site.test"], True), ("test", ["https://site.test"], 0),
])
def test_invalid_policy_configuration(provider, origins, limit):
    with pytest.raises(BrowserPolicyError):
        BrowserPolicy(provider, origins, max_body_bytes=limit)
