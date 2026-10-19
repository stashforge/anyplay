import json
import logging
import time
import urllib.error
from unittest.mock import MagicMock, Mock
from urllib.parse import parse_qs, urlsplit

import pytest

from media_cli.media import aniskip


def response_item(**overrides):
    item = {
        "interval": {"startTime": 4784, "endTime": 4873},
        "skipType": "ed",
        "skipId": "ending-4921",
        "episodeLength": 4921,
    }
    item.update(overrides)
    return item


def response(items=None, *, payload=None):
    if payload is None:
        payload = {"found": True, "results": items, "message": "Found skip times", "statusCode": 200}
    result = MagicMock()
    result.__enter__.return_value = result
    result.status = 200
    result.read.return_value = json.dumps(payload).encode("utf-8")
    return result


@pytest.fixture(autouse=True)
def network(monkeypatch):
    monkeypatch.setattr(aniskip, "_ANISKIP_CACHE", {})
    mocked = Mock(return_value=response([response_item()]))
    monkeypatch.setattr(aniskip.urllib.request, "urlopen", mocked)
    monkeypatch.setattr(time, "sleep", lambda _: None)
    return mocked


def candidate(**overrides):
    item = {
        "type": "ed",
        "start": 4784.0,
        "end": 4873.0,
        "label": "Ending",
        "episode_length": 4921.0,
        "skip_id": "ending-4921",
    }
    item.update(overrides)
    return item


def test_fetch_preserves_response_fields_and_default_callers(network):
    assert aniskip.fetch_skip_times(123, 1) == [candidate()]
    query = parse_qs(urlsplit(network.call_args.args[0].full_url).query)
    assert float(query["episodeLength"][0]) == 0.0
    assert set(query["types"]) == {"op", "ed", "recap", "mixed-op", "mixed-ed"}


@pytest.mark.parametrize("duration, accepted", [(1465.0, False), (4921.0, True)])
def test_fetch_uses_actual_duration_and_rejects_other_editions(network, duration, accepted):
    result = aniskip.fetch_skip_times(123, 1, episode_length=duration)
    assert result == ([candidate()] if accepted else [])
    query = parse_qs(urlsplit(network.call_args.args[0].full_url).query)
    assert float(query["episodeLength"][0]) == duration


def test_unknown_runtime_candidates_can_be_validated_later():
    intervals = aniskip.fetch_skip_times(123, 1)
    assert intervals == [candidate()]
    assert aniskip.validated_skip_intervals(intervals, 1465) == []
    assert aniskip.validated_skip_intervals(intervals, 4921) == [candidate()]


def test_cache_isolates_duration_and_normalizes_equivalent_episode_numbers(network):
    assert aniskip.fetch_skip_times(123, 1, episode_length=1465) == []
    assert aniskip.fetch_skip_times(123, 1, episode_length=4921) == [candidate()]
    assert aniskip.fetch_skip_times(123, 1.0, episode_length=4921.0) == [candidate()]
    assert aniskip.fetch_skip_times(123, 1) == [candidate()]
    assert network.call_count == 3


def test_cache_isolates_mal_and_episode(network):
    for mal_id, episode in [(123, 1), (124, 1), (123, 1.5), (123, 1)]:
        assert aniskip.fetch_skip_times(mal_id, episode) == [candidate()]
    assert network.call_count == 3


def test_cache_isolates_types_and_canonicalizes_order_and_duplicates(network):
    opening = response_item(interval={"startTime": 0, "endTime": 90}, skipType="op", skipId="opening")
    network.return_value = response([opening])
    assert [item["type"] for item in aniskip.fetch_skip_times(123, 1, skip_types=["op"])] == ["op"]
    network.return_value = response([response_item()])
    assert aniskip.fetch_skip_times(123, 1, skip_types=["ed"]) == [candidate()]
    network.return_value = response([opening, response_item()])
    assert len(aniskip.fetch_skip_times(123, 1, skip_types=["op", "ed"])) == 2
    assert len(aniskip.fetch_skip_times(123, 1, skip_types=["ed", "op", "op"])) == 2
    assert network.call_count == 3
    query = parse_qs(urlsplit(network.call_args.args[0].full_url).query)
    assert sorted(query["types"]) == ["ed", "op"]


def test_consumer_mutations_cannot_poison_new_or_cached_results(network):
    first = aniskip.fetch_skip_times(123, 1)
    first[0]["start"] = -500
    first[0]["episode_length"] = 1465
    first.append({"type": "op"})
    second = aniskip.fetch_skip_times(123, 1)
    assert second == [candidate()]
    second[0]["end"] = 99999
    second.clear()
    assert aniskip.fetch_skip_times(123, 1) == [candidate()]
    assert network.call_count == 1


def test_cached_peek_misses_never_use_network(network):
    network.side_effect = AssertionError("Cache peeks must not access the network")
    assert aniskip.cached_skip_times(123, 1) == []
    assert aniskip.cached_skip_times(None, 1) == []
    assert network.call_count == 0


def test_cached_peek_matches_request_and_returns_copies(network):
    aniskip.fetch_skip_times(123, 1, episode_length=4921, skip_types=["ed", "op"])
    network.reset_mock()
    network.side_effect = AssertionError("Cache peeks must not access the network")
    first = aniskip.cached_skip_times(123, 1.0, episode_length=4921, skip_types=["op", "ed", "ed"])
    assert first == [candidate()]
    first[0]["start"] = -1
    first.append({})
    assert aniskip.cached_skip_times(123, 1, episode_length=4921, skip_types=["ed", "op"]) == [candidate()]
    assert aniskip.cached_skip_times(123, 1, episode_length=1465, skip_types=["ed", "op"]) == []
    assert aniskip.cached_skip_times(123, 1, episode_length=4921, skip_types=["op"]) == []
    assert aniskip.cached_skip_times(124, 1, episode_length=4921, skip_types=["ed", "op"]) == []
    assert aniskip.cached_skip_times(123, 2, episode_length=4921, skip_types=["ed", "op"]) == []
    assert aniskip.cached_skip_times(123, 1) == []
    assert network.call_count == 0


def test_cached_peek_exposes_default_prefetch_candidates_without_network(network):
    aniskip.fetch_skip_times(123, 1)
    network.reset_mock()
    assert aniskip.cached_skip_times(123, 1) == [candidate()]
    assert network.call_count == 0


@pytest.mark.parametrize("collection", [tuple, iter])
def test_validation_accepts_candidate_iterables(collection):
    assert aniskip.validated_skip_intervals(collection([candidate()]), 4921) == [candidate()]


@pytest.mark.parametrize("field, value", [("start", False), ("end", True), ("episode_length", True)])
def test_validation_rejects_boolean_numbers(field, value):
    item = candidate(start=0, end=0.5, episode_length=2)
    item[field] = value
    assert aniskip.validated_skip_intervals([item], 2) == []


def test_validation_rejects_end_beyond_reference_even_within_actual_runtime():
    assert aniskip.validated_skip_intervals([candidate(end=4921, episode_length=4901)], 4921) == []


@pytest.mark.parametrize(
    "options",
    [
        {"mal_id": -1},
        {"mal_id": 0},
        {"skip_types": ["unknown"]},
        {"episode_number": float("inf")},
        {"episode_number": float("nan")},
    ],
)
def test_invalid_request_is_not_sent_or_cached(network, options):
    request = {"mal_id": 123, "episode_number": 1}
    request.update(options)
    assert aniskip.fetch_skip_times(**request) == []
    assert aniskip.cached_skip_times(**request) == []
    assert network.call_count == 0


def test_unexpected_programming_errors_are_not_silenced(network):
    network.side_effect = RuntimeError("unexpected bug")
    with pytest.raises(RuntimeError, match="unexpected bug"):
        aniskip.fetch_skip_times(123, 1)


@pytest.mark.parametrize(
    "bad_item",
    [
        None,
        [],
        "corrupt",
        {},
        response_item(interval=None),
        response_item(interval=[]),
        response_item(interval={"endTime": 90}),
        response_item(interval={"startTime": 0}),
        response_item(interval={"startTime": "bad", "endTime": 90}),
        response_item(interval={"startTime": -1, "endTime": 90}),
        response_item(interval={"startTime": True, "endTime": 90}),
        response_item(interval={"startTime": 0, "endTime": True}),
        response_item(interval={"startTime": 90, "endTime": 90}),
        response_item(interval={"startTime": 91, "endTime": 90}),
        response_item(interval={"startTime": float("nan"), "endTime": 90}),
        response_item(interval={"startTime": 0, "endTime": float("inf")}),
        response_item(interval={"startTime": float("-inf"), "endTime": 90}),
        response_item(skipType="unknown-op"),
        response_item(skipType=""),
        response_item(skipType=None),
        response_item(skipType=[]),
        response_item(episodeLength="bad"),
        response_item(episodeLength=None),
        response_item(episodeLength=0),
        response_item(episodeLength=-1),
        response_item(episodeLength=True),
        response_item(episodeLength=4872),
        response_item(episodeLength=float("nan")),
        response_item(episodeLength=float("inf")),
        response_item(episodeLength=float("-inf")),
    ],
)
def test_malformed_response_items_do_not_discard_valid_neighbors(network, bad_item):
    network.return_value = response([response_item(), bad_item, response_item(skipId="second")])
    assert aniskip.fetch_skip_times(123, 1) == [candidate(), candidate(skip_id="second")]
    assert network.call_count == 1


@pytest.mark.parametrize(
    "skip_type, label",
    [("op", "Opening"), ("ed", "Ending"), ("recap", "Recap"), ("mixed-op", "Opening"), ("mixed-ed", "Ending")],
)
def test_recognized_response_types_keep_labels(network, skip_type, label):
    network.return_value = response([response_item(skipType=skip_type)])
    assert aniskip.fetch_skip_times(123, 1) == [candidate(type=skip_type, label=label)]


@pytest.mark.parametrize("duration", [None, "bad", 0, -1, float("nan"), float("inf"), float("-inf")])
def test_validation_requires_finite_positive_actual_duration(duration):
    assert aniskip.validated_skip_intervals([candidate()], duration) == []


@pytest.mark.parametrize(
    "bad_interval",
    [
        None,
        [],
        "corrupt",
        {},
        candidate(type="unknown-op"),
        candidate(type=[]),
        candidate(start=None),
        candidate(start="bad"),
        candidate(start=-1),
        candidate(start=float("nan")),
        candidate(start=float("inf")),
        candidate(start=float("-inf")),
        candidate(end=None),
        candidate(end="bad"),
        candidate(end=4784),
        candidate(end=4783),
        candidate(end=4921.01),
        candidate(end=float("nan")),
        candidate(end=float("inf")),
        candidate(end=float("-inf")),
        candidate(episode_length=None),
        candidate(episode_length="bad"),
        candidate(episode_length=0),
        candidate(episode_length=-1),
        candidate(episode_length=float("nan")),
        candidate(episode_length=float("inf")),
        candidate(episode_length=float("-inf")),
        candidate(episode_length=4900.99),
        candidate(episode_length=4941.01),
        {"type": "op", "start": 0, "end": 90, "label": "Opening"},
    ],
)
def test_external_validation_rejects_bad_dicts_independently_without_mutation(bad_interval):
    valid = candidate()
    assert aniskip.validated_skip_intervals([bad_interval, valid, bad_interval], 4921) == [candidate()]
    assert valid == candidate()


@pytest.mark.parametrize("reference", [4901, 4921, 4941])
def test_validation_accepts_twenty_second_boundary_without_scaling_or_clamping(reference):
    item = candidate(start=0.25, end=4901, episode_length=reference)
    assert aniskip.validated_skip_intervals([item], 4921) == [item]


@pytest.mark.parametrize("intervals", [None, 123, "bad", {}, {"type": "op"}])
def test_validation_handles_malformed_interval_collections(intervals):
    assert aniskip.validated_skip_intervals(intervals, 4921) == []


@pytest.mark.parametrize(
    "error",
    [
        urllib.error.URLError("offline"),
        TimeoutError("timed out"),
        urllib.error.HTTPError("https://example.invalid", 403, "Forbidden", {}, None),
        urllib.error.HTTPError("https://example.invalid", 404, "Not Found", {}, None),
        urllib.error.HTTPError("https://example.invalid", 503, "Unavailable", {}, None),
    ],
)
def test_failed_requests_are_expected_and_not_cached(network, caplog, error):
    network.side_effect = error
    with caplog.at_level(logging.DEBUG):
        assert aniskip.fetch_skip_times(123, 1) == []
    assert not [record for record in caplog.records if record.levelno >= logging.WARNING]
    attempts = network.call_count
    network.side_effect = None
    assert aniskip.fetch_skip_times(123, 1) == [candidate()]
    assert network.call_count == attempts + 1


@pytest.mark.parametrize("payload", [None, [], {"found": True, "results": "bad"}])
def test_corrupt_response_envelopes_are_not_cached(network, payload):
    broken = response([])
    broken.read.return_value = json.dumps(payload).encode("utf-8")
    network.return_value = broken
    assert aniskip.fetch_skip_times(123, 1) == []
    attempts = network.call_count
    network.return_value = response([response_item()])
    assert aniskip.fetch_skip_times(123, 1) == [candidate()]
    assert network.call_count == attempts + 1


def test_successful_empty_result_is_cached(network):
    network.return_value = response([], payload={"found": False, "results": []})
    assert aniskip.fetch_skip_times(123, 1) == []
    assert aniskip.fetch_skip_times(123, 1) == []
    assert network.call_count == 1


def test_debug_logs_include_rejected_reference_actual_duration_and_request_runtime(caplog):
    with caplog.at_level(logging.DEBUG, logger=aniskip.__name__):
        assert aniskip.fetch_skip_times(123, 1, episode_length=1465) == []
    messages = caplog.text.lower()
    assert "reject" in messages
    assert "4921" in messages
    assert "1465" in messages
    assert "request" in messages
    assert "runtime" in messages


def test_generate_chapters_file_behavior_is_preserved(tmp_path):
    target = tmp_path / "chapters.ffmeta"
    intervals = [{"type": "op", "label": "Opening", "start": 10, "end": 90}]
    assert aniskip.generate_chapters_file(intervals, str(target)) == str(target)
    assert target.read_text() == (
        ";FFMETADATA1\n\n[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=10000\nTITLE=Intro\n"
        "\n[CHAPTER]\nTIMEBASE=1/1000\nSTART=10000\nEND=90000\nTITLE=Opening\n"
        "\n[CHAPTER]\nTIMEBASE=1/1000\nSTART=90000\nEND=390000\nTITLE=Episode\n"
    )
    assert aniskip.generate_chapters_file([], str(target)) == ""
    assert not target.exists()
