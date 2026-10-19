from concurrent.futures import ThreadPoolExecutor

import pytest

from media_cli.ui import player_screen


def test_obsolete_background_listener_is_removed():
    for name in ("_loading_input_thread", "_loading_input_stop_event",
                 "_start_loading_input_thread", "_stop_loading_input_thread",
                 "_loading_input_owned_elsewhere"):
        assert not hasattr(player_screen, name), name


def test_obsolete_background_renderer_and_snapshot_are_removed():
    for name in ("start_loading_ticker", "_ticker_thread", "_ticker_stop_event",
                 "_player_loading_task"):
        assert not hasattr(player_screen, name), name


def test_player_status_uses_only_the_supplied_snapshot(monkeypatch):
    from media_cli.ui.actions import ActionBindings
    from media_cli.ui.loading_model import LoadingInteraction, LoadingOwner, LoadingPlacement, LoadingTaskSpec
    from media_cli.ui.player_loading import PlayerLoadingState

    loading = PlayerLoadingState()
    loading.start(LoadingTaskSpec(
        "lookup", LoadingOwner("player", 0), "Finding your episode...",
        LoadingInteraction.BLOCKING, LoadingPlacement.PLAYER_STATUS, ActionBindings(()), delay=0,
    ), elapsed_offset=5)
    state = {"loading_provider_name": "Example", "loading_start_time": 10}
    monkeypatch.setattr(player_screen.time, "time", lambda: 100)
    line = player_screen._loading_status_line(state, loading.snapshot)
    assert "Finding your episode... (5s)" in line
    assert "Loading stream" not in line
    # A following frame without a task must not reuse the previous snapshot.
    line = player_screen._loading_status_line(state)
    assert "Loading stream from Example... (90s)" in line
    assert "Finding your episode" not in line


def test_player_does_not_keep_legacy_terminal_descriptors_or_hooks():
    for name in ("stop_loading_ticker", "_stop_loading_for_handoff",
                 "_poll_loading_cancel_input", "_open_loading_input",
                 "_open_loading_stdin_input", "_close_loading_input",
                 "_close_loading_stdin_input", "_release_loading_input",
                 "_loading_key_reader", "_loading_input_lock",
                 "_loading_stdin_fd", "_loading_tty_fd"):
        assert not hasattr(player_screen, name), name


def test_workers_cannot_borrow_playback_input():
    def borrow():
        with player_screen.playback_input_scope():
            pytest.fail("Worker acquired playback input")

    with ThreadPoolExecutor(max_workers=1) as pool:
        with pytest.raises(RuntimeError, match="main UI thread"):
            pool.submit(borrow).result(timeout=1)


def test_production_code_has_no_legacy_loading_cleanup_references():
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "media_cli"
    references = []
    for path in root.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            name = (node.id if isinstance(node, ast.Name) else
                    node.attr if isinstance(node, ast.Attribute) else
                    node.name if isinstance(node, ast.alias) else None)
            if name == "stop_loading_ticker":
                references.append(str(path.relative_to(root)))
    assert not references, references
