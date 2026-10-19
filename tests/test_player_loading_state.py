from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from media_cli.ui.actions import ActionBindings
from media_cli.ui.loading_model import (
    LoadingInteraction,
    LoadingOwner,
    LoadingPlacement,
    LoadingTaskSpec,
)
from media_cli.ui.player_loading import PlayerLoadingState


def task(**changes):
    return replace(LoadingTaskSpec(
        "stream", LoadingOwner("player", 1), "Loading stream...",
        LoadingInteraction.BLOCKING, LoadingPlacement.PLAYER_STATUS,
        ActionBindings(()), delay=0,
    ), **changes)


def test_player_loading_publishes_immutable_snapshots_without_restarting():
    now = [0.0]
    state = PlayerLoadingState(clock=lambda: now[0])
    assert state.snapshot is None
    state.start(task())
    first = state.snapshot
    now[0] = 3
    state.update("Trying another source...")
    updated = state.snapshot
    assert updated.token == first.token
    assert updated.elapsed == 3
    assert updated.message == "Trying another source..."
    assert first.message == "Loading stream..."
    assert first.elapsed == 0
    now[0] = 5
    state.tick()
    assert state.snapshot.elapsed == 5
    assert updated.elapsed == 3


def test_player_snapshot_can_be_read_from_other_thread_but_not_mutated():
    state = PlayerLoadingState()
    state.start(task())
    snapshot = state.snapshot
    with ThreadPoolExecutor(max_workers=1) as pool:
        assert pool.submit(lambda: state.snapshot).result() is snapshot
        with pytest.raises(RuntimeError, match="owner thread"):
            pool.submit(state.update, "Wrong thread").result()
    assert state.snapshot is snapshot


def test_player_timer_includes_loading_before_ticker_started():
    now = [0.0]
    state = PlayerLoadingState(clock=lambda: now[0])
    state.start(task(), elapsed_offset=4)
    assert state.snapshot.elapsed == 4
    now[0] = 3
    state.tick()
    assert state.snapshot.elapsed == 7


@pytest.mark.parametrize("cancelled", [False, True])
def test_player_loading_retires_and_can_start_a_new_attempt(cancelled):
    state = PlayerLoadingState()
    state.start(task())
    first = state.snapshot.token
    state.finish(cancelled=cancelled)
    assert state.snapshot is None
    state.finish()
    state.start(task())
    assert state.snapshot.token != first


def test_player_loading_rejects_invalid_updates_without_changing_snapshot():
    state = PlayerLoadingState()
    state.start(task())
    before = state.snapshot
    with pytest.raises(ValueError):
        state.update("x" * 121)
    assert state.snapshot is before
    with pytest.raises(RuntimeError, match="already active"):
        state.start(task())
    assert state.snapshot is before


def test_player_loading_requires_player_placement_and_active_updates():
    state = PlayerLoadingState()
    with pytest.raises(ValueError, match="player status"):
        state.start(task(placement=LoadingPlacement.FOOTER))
    with pytest.raises(ValueError, match="player status"):
        state.start(task(transition=True))
    with pytest.raises(RuntimeError, match="active"):
        state.update("Too early")
    assert state.snapshot is None
