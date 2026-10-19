from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from media_cli.ui.actions import ActionAvailability, ActionBindings, ResolvedAction
from media_cli.ui.loading_lifecycle import LoadingLifecycle
from media_cli.ui.loading_model import (
    CancelLoading,
    FailLoading,
    FinishLoading,
    LoadingInteraction,
    LoadingOwner,
    LoadingPhase,
    LoadingPlacement,
    LoadingProgress,
    LoadingTaskSpec,
    UpdateLoading,
)
from media_cli.ui.message_choices import MessagePicker
from media_cli.ui.picker_actions import picker_bindings

OWNER = LoadingOwner("results", 1)


def task(name="episodes", **changes):
    return replace(LoadingTaskSpec(
        name, OWNER, "Fetching episodes", LoadingInteraction.BLOCKING,
        LoadingPlacement.FOOTER, picker_bindings(),
    ), **changes)


@pytest.fixture
def lifecycle():
    now = [0.0]
    return LoadingLifecycle(clock=lambda: now[0]), now


def test_delay_elapsed_updates_and_progress_clear(lifecycle):
    ui, now = lifecycle
    token = ui.start(task(progress=LoadingProgress(1, 2)))
    now[0] = 0.149
    assert not ui.snapshots(OWNER)[0].visible
    now[0] = 0.150
    snapshot = ui.snapshots(OWNER)[0]
    assert snapshot.visible and snapshot.elapsed == 0.15
    assert snapshot.phase is LoadingPhase.RUNNING
    assert ui.apply(UpdateLoading(token, "Preparing", LoadingProgress(2, 2)))
    assert ui.snapshots(OWNER)[0].progress == LoadingProgress(2, 2)
    assert ui.apply(UpdateLoading(token, "Ready soon"))
    assert ui.snapshots(OWNER)[0].message == "Ready soon"
    assert ui.snapshots(OWNER)[0].progress is None


def test_loading_rotates_shared_messages_but_progress_and_resume_reset_timer():
    now = [0.0]
    ui = LoadingLifecycle(
        clock=lambda: now[0], message_picker=MessagePicker(choose=lambda options: options[0]),
        rotation_interval=lambda: 4,
    )
    token = ui.start(task(message=["Fetching episodes", "Getting the episode list"]))
    assert ui.snapshots(OWNER)[0].message == "Fetching episodes"
    now[0] = 3.99
    assert ui.snapshots(OWNER)[0].message == "Fetching episodes"
    now[0] = 4
    assert ui.snapshots(OWNER)[0].message == "Getting the episode list"
    assert ui.snapshots(OWNER)[0].elapsed == 4
    now[0] = 7
    ui.apply(UpdateLoading(token, ("Fetching episodes", "Getting the episode list"), LoadingProgress(1, 2)))
    now[0] = 8
    assert ui.snapshots(OWNER)[0].message == "Getting the episode list"
    ui.suspend(token)
    now[0] = 40
    ui.resume(token)
    now[0] = 40.2
    assert ui.snapshots(OWNER)[0].message == "Getting the episode list"
    now[0] = 44
    assert ui.snapshots(OWNER)[0].message == "Fetching episodes"
    ui.apply(UpdateLoading(token, "Found two episodes", LoadingProgress(2, 2)))
    now[0] = 100
    assert ui.snapshots(OWNER)[0].message == "Found two episodes"


def test_data_ready_transition_stops_rotating():
    now = [0.0]
    ui = LoadingLifecycle(clock=lambda: now[0], rotation_interval=lambda: 3)
    token = ui.start(task(message=("One", "Two"), transition=True))
    message = ui.snapshots(OWNER)[0].message
    ui.apply(FinishLoading(token))
    now[0] = 100
    assert ui.snapshots(OWNER)[0].message == message


def test_loading_feedback_is_owned_by_task_and_plain_snapshots_stay_original(lifecycle):
    ui, now = lifecycle
    token = ui.start(task())
    binding = ui.snapshots(OWNER)[0].actions.resolve("ENTER")
    assert ui.feedback(token, binding)
    assert ui.snapshots(OWNER, feedback=True)[0].message == "Hang on, still loading."
    assert ui.snapshots(OWNER)[0].message == "Fetching episodes"
    ui.apply(UpdateLoading(token, "Newest task message"))
    now[0] = 1
    assert ui.snapshots(OWNER, feedback=True)[0].message == "Newest task message"
    ui.suspend(token)
    assert not ui.feedback(token, binding)
    ui.resume(token)
    ui.apply(CancelLoading(token))
    assert not ui.feedback(token, binding)


def test_background_tasks_cannot_steal_or_show_blocking_feedback(lifecycle):
    ui, _ = lifecycle
    foreground = ui.start(task())
    background = ui.start(task("cover", interaction=LoadingInteraction.BACKGROUND))
    binding = ui.snapshots(OWNER)[0].actions.resolve("ENTER")
    assert not ui.feedback(background, binding)
    assert ui.feedback(foreground, binding)
    assert ui.snapshots(OWNER, feedback=True)[0].message == "Hang on, still loading."
    assert ui.snapshots(OWNER, feedback=True)[1].message == "Fetching episodes"


def test_blocking_before_background_and_stable_order(lifecycle):
    ui, _ = lifecycle
    background = ui.start(task("cover", interaction=LoadingInteraction.BACKGROUND))
    first = ui.start(task("episodes"))
    second = ui.start(task("details"))
    assert tuple(s.token for s in ui.snapshots(OWNER)) == (first, second, background)
    other = LoadingOwner("results", 2)
    ui.start(task(owner=other))
    assert len(ui.snapshots(other)) == 1
    assert len(ui.snapshots(OWNER)) == 3


def test_background_preserves_bindings_and_blocking_respects_disabled_cancel(lifecycle):
    ui, _ = lifecycle
    bindings = picker_bindings()
    blocked_back = ActionBindings(tuple(
        ResolvedAction(b.action, ActionAvailability.BLOCKED, "Cannot cancel")
        if b.action.id == "back" else b for b in bindings.items
    ))
    ui.start(task(actions=blocked_back))
    ui.start(task("cover", actions=bindings, interaction=LoadingInteraction.BACKGROUND))
    blocking, background = ui.snapshots(OWNER)
    assert blocking.actions.resolve("UP").availability is ActionAvailability.BLOCKED
    assert blocking.actions.resolve("ESC").blocked_message == "Cannot cancel"
    assert blocking.actions.resolve("CTRL_C").availability is ActionAvailability.AVAILABLE
    assert background.actions is bindings


def test_rebind_refreshes_source_capabilities_without_resetting_task(lifecycle):
    ui, now = lifecycle
    token = ui.start(task())
    now[0] = 1.0
    assert ui.rebind(token, picker_bindings(refresh=True))
    snapshot = ui.snapshots(OWNER)[0]
    assert snapshot.actions.resolve("F5").availability is ActionAvailability.BLOCKED
    assert snapshot.message == "Fetching episodes" and snapshot.elapsed == 1.0
    with pytest.raises(TypeError):
        ui.rebind(token, ())
    ui.apply(CancelLoading(token))
    assert not ui.rebind(token, picker_bindings())


@pytest.mark.parametrize("event", (FinishLoading, CancelLoading, lambda t: FailLoading(t, "Unavailable")))
def test_terminal_events_retire_task_and_ignore_late_update(lifecycle, event):
    ui, _ = lifecycle
    token = ui.start(task())
    assert ui.apply(event(token))
    assert ui.snapshots(OWNER) == ()
    assert not ui.apply(UpdateLoading(token, "Too late"))
    assert not ui.apply(event(token))


def test_task_id_reuse_does_not_accept_old_incarnation(lifecycle):
    ui, _ = lifecycle
    old = ui.start(task())
    with pytest.raises(ValueError):
        ui.start(task())
    ui.apply(CancelLoading(old))
    new = ui.start(task())
    assert old != new
    assert not ui.apply(FinishLoading(old))
    assert ui.snapshots(OWNER)[0].token == new


def test_tokens_from_another_controller_cannot_address_local_task(lifecycle):
    ui, _ = lifecycle
    local = ui.start(task())
    foreign = LoadingLifecycle().start(task())
    assert local != foreign
    assert not ui.apply(FinishLoading(foreign))


def test_transition_waits_for_first_frame(lifecycle):
    ui, _ = lifecycle
    token = ui.start(task(transition=True))
    with pytest.raises(ValueError):
        ui.acknowledge_frame(token)
    assert ui.apply(FinishLoading(token))
    assert ui.snapshots(OWNER)[0].phase is LoadingPhase.DATA_READY
    assert not ui.apply(FinishLoading(token))
    assert not ui.apply(UpdateLoading(token, "Late worker update"))
    assert ui.acknowledge_frame(token)
    assert ui.snapshots(OWNER) == ()
    assert not ui.acknowledge_frame(token)


def test_ordinary_task_cannot_acknowledge_destination(lifecycle):
    ui, _ = lifecycle
    with pytest.raises(ValueError):
        ui.acknowledge_frame(ui.start(task()))


def test_nested_suspend_updates_resume_and_child_finish(lifecycle):
    ui, now = lifecycle
    parent = ui.start(task())
    now[0] = 1.0
    assert ui.suspend(parent)
    child = ui.start(task("child"), parent=parent)
    assert tuple(s.token for s in ui.snapshots(OWNER)) == (child,)
    ui.apply(UpdateLoading(parent, "Newest parent status"))
    ui.apply(FinishLoading(child))
    assert ui.snapshots(OWNER) == ()
    now[0] = 2.0
    assert ui.resume(parent)
    snapshot = ui.snapshots(OWNER)[0]
    assert snapshot.message == "Newest parent status"
    assert snapshot.elapsed == 2.0 and not snapshot.visible
    now[0] = 2.16
    assert ui.snapshots(OWNER)[0].visible
    assert ui.resume(parent) is False  # Must not restart appearance delay again.


def test_retiring_parent_detaches_descendants(lifecycle):
    ui, _ = lifecycle
    parent = ui.start(task())
    child = ui.start(task("child"), parent=parent)
    grandchild = ui.start(task("grandchild"), parent=child)
    ui.apply(CancelLoading(parent))
    assert ui.snapshots(OWNER) == ()
    assert not ui.apply(FinishLoading(grandchild))
    assert not ui.suspend(parent)
    assert not ui.resume(child)


def test_detach_only_target_owner_and_validate_parent(lifecycle):
    ui, _ = lifecycle
    parent = ui.start(task())
    other_owner = LoadingOwner("other", 0)
    foreign = ui.start(task(owner=other_owner))
    with pytest.raises(ValueError):
        ui.start(task("child"), parent=foreign)
    ui.detach(OWNER)
    assert ui.snapshots(OWNER) == ()
    assert ui.snapshots(other_owner)[0].token == foreign
    with pytest.raises(ValueError):
        ui.start(task("child"), parent=parent)


@pytest.mark.parametrize("method,args", (
    ("start", (None,)), ("apply", (None,)), ("acknowledge_frame", (None,)),
    ("suspend", (None,)), ("resume", (None,)), ("detach", (None,)),
    ("snapshots", (None,)),
))
def test_wrong_public_arguments_fail_immediately(lifecycle, method, args):
    ui, _ = lifecycle
    with pytest.raises(TypeError):
        getattr(ui, method)(*args)


def test_worker_posts_but_only_owner_applies(lifecycle):
    ui, _ = lifecycle
    token = ui.start(task())
    with ThreadPoolExecutor(max_workers=1) as worker:
        worker.submit(ui.post, UpdateLoading(token, "Worker update")).result()
    assert ui.snapshots(OWNER)[0].message == "Fetching episodes"
    assert ui.drain() == 1
    assert ui.snapshots(OWNER)[0].message == "Worker update"
    assert ui.drain() == 0


@pytest.mark.parametrize("method", (
    "start", "apply", "drain", "acknowledge_frame", "suspend", "resume", "detach", "snapshots", "rebind",
))
def test_worker_cannot_mutate_or_inspect_state(lifecycle, method):
    ui, _ = lifecycle
    token = ui.start(task())
    args = {
        "start": (task("child"),), "apply": (FinishLoading(token),), "drain": (),
        "acknowledge_frame": (token,), "suspend": (token,), "resume": (token,),
        "detach": (OWNER,), "snapshots": (OWNER,),
        "rebind": (token, picker_bindings()),
    }[method]
    with ThreadPoolExecutor(max_workers=1) as worker:
        with pytest.raises(RuntimeError, match="owner thread"):
            worker.submit(getattr(ui, method), *args).result()


def test_queued_old_update_cannot_revive_detached_task(lifecycle):
    ui, _ = lifecycle
    token = ui.start(task())
    ui.post(UpdateLoading(token, "Old worker"))
    ui.detach(OWNER)
    new = ui.start(task())
    assert ui.drain() == 1
    assert ui.snapshots(OWNER)[0].token == new
    assert ui.snapshots(OWNER)[0].message == "Fetching episodes"


def test_drain_is_bounded_and_preserves_producer_order(lifecycle):
    ui, _ = lifecycle
    token = ui.start(task())
    for index in range(257):
        ui.post(UpdateLoading(token, f"Update {index}"))
    assert ui.drain() == 256
    assert ui.snapshots(OWNER)[0].message == "Update 255"
    assert ui.drain() == 1
    assert ui.snapshots(OWNER)[0].message == "Update 256"


def test_invalid_publication_fails_before_queueing(lifecycle):
    ui, _ = lifecycle
    with pytest.raises(TypeError):
        ui.post("message")
    assert ui.drain() == 0


def test_log_records_identity_not_sensitive_messages(lifecycle, caplog):
    ui, _ = lifecycle
    with caplog.at_level("DEBUG", logger="media_cli.ui.loading_lifecycle"):
        token = ui.start(task(message="secret-stream-credential"))
        ui.apply(FailLoading(token, "another-secret"))
        ui.post(UpdateLoading(token, "late-secret"))
        ui.drain()
    assert "owner=results" in caplog.text
    assert "activation=1" in caplog.text
    assert "stale event ignored" in caplog.text
    assert "failed" in caplog.text
    assert "secret" not in caplog.text
