from dataclasses import FrozenInstanceError, replace

import pytest

from media_cli.ui.actions import ActionBindings
from media_cli.ui.loading_model import (
    CancelLoading,
    FailLoading,
    FinishLoading,
    LoadingInteraction,
    LoadingOwner,
    LoadingPhase,
    LoadingPlacement,
    LoadingProgress,
    LoadingSnapshot,
    LoadingTaskSpec,
    LoadingToken,
    UpdateLoading,
)


def spec(**changes):
    return replace(LoadingTaskSpec(
        "episodes", LoadingOwner("results", 0), "Fetching episodes",
        LoadingInteraction.BLOCKING, LoadingPlacement.FOOTER, ActionBindings(()),
    ), **changes)


def test_records_are_immutable_and_progress_can_be_empty():
    task = spec(progress=LoadingProgress(0, 0))
    assert task.delay == 0.15
    assert task.actions.items == ()
    with pytest.raises(FrozenInstanceError):
        task.message = "changed"


def test_loading_input_supports_immutable_message_alternatives():
    values = ["Fetching episodes", "Getting the episode list"]
    task = spec(message=values)
    values.append("Changed after construction")
    assert task.message == ("Fetching episodes", "Getting the episode list")
    token = LoadingToken(task.owner, task.task_id, 1)
    assert UpdateLoading(token, ["Preparing", "Getting things ready"]).message == (
        "Preparing", "Getting things ready",
    )


@pytest.mark.parametrize("message", ("x" * 121, [f"Message {i}" for i in range(11)], ["Same", "Same"]))
def test_task_and_update_enforce_message_caps(message):
    with pytest.raises(ValueError, match="task.message"):
        spec(message=message)
    with pytest.raises(ValueError, match="update.message"):
        UpdateLoading(LoadingToken(LoadingOwner("results", 0), "episodes", 1), message)


@pytest.mark.parametrize("message", ("", "   ", "x\n", "x\t", "\x1b[31mred", "x\x7f", "x\x85"))
def test_messages_and_identifiers_reject_blank_or_control_text(message):
    for field in ("message", "task_id"):
        with pytest.raises(ValueError):
            spec(**{field: message})
    with pytest.raises(ValueError):
        LoadingOwner(message, 0)


@pytest.mark.parametrize("field,value", (
    ("task_id", None), ("message", 10), ("owner", "results"),
    ("interaction", "blocking"), ("placement", "footer"),
    ("actions", ()), ("progress", {}), ("transition", 1),
    ("delay", True), ("delay", "0.15"),
))
def test_task_rejects_wrong_field_types(field, value):
    with pytest.raises(TypeError):
        spec(**{field: value})


@pytest.mark.parametrize("delay", (-1, float("nan"), float("inf"), -float("inf")))
def test_delay_requires_finite_nonnegative_value(delay):
    with pytest.raises(ValueError):
        spec(delay=delay)


@pytest.mark.parametrize("value", (True, 1.5, "1", None))
def test_counts_require_integers_not_bool(value):
    for factory in (
        lambda: LoadingOwner("results", value),
        lambda: LoadingToken(LoadingOwner("results", 0), "episodes", value),
        lambda: LoadingProgress(value),
        lambda: LoadingProgress(0, value) if value is not None else LoadingProgress(value),
    ):
        with pytest.raises(TypeError):
            factory()


@pytest.mark.parametrize("completed,total", ((-1, None), (1, 0), (0, -1)))
def test_progress_bounds(completed, total):
    with pytest.raises(ValueError):
        LoadingProgress(completed, total)


def test_owner_and_token_bounds_and_types():
    with pytest.raises(ValueError):
        LoadingOwner("results", -1)
    for incarnation in (0, -1):
        with pytest.raises(ValueError):
            LoadingToken(LoadingOwner("results", 0), "episodes", incarnation)
    with pytest.raises(TypeError):
        LoadingToken("results", "episodes", 1)


def test_addressed_events_validate_tokens_messages_and_progress():
    token = LoadingToken(LoadingOwner("results", 0), "episodes", 1)
    assert UpdateLoading(token, "Reading").progress is None
    assert FinishLoading(token).token == token
    assert CancelLoading(token).token == token
    assert FailLoading(token, "Unavailable").message == "Unavailable"
    for factory in (FinishLoading, CancelLoading):
        with pytest.raises(TypeError):
            factory("bad")
    for factory in (UpdateLoading, FailLoading):
        with pytest.raises(TypeError):
            factory("bad", "Reading")
        with pytest.raises(ValueError):
            factory(token, "")
    with pytest.raises(TypeError):
        UpdateLoading(token, "Reading", {})


def test_snapshot_validates_supplied_render_data():
    task = spec()
    snapshot = LoadingSnapshot(
        LoadingToken(task.owner, task.task_id, 1), task.message, LoadingPhase.RUNNING,
        task.interaction, task.placement, task.actions, False, 0.0, None,
    )
    for field, value in (
        ("phase", "running"), ("visible", 0), ("elapsed", True),
        ("progress", {}), ("token", None), ("actions", ()),
    ):
        with pytest.raises(TypeError):
            replace(snapshot, **{field: value})
    for elapsed in (-1, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            replace(snapshot, elapsed=elapsed)
