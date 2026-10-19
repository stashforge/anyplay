import threading


def test_preparing_details_does_not_change_source_until_commit(monkeypatch):
    from media_cli import app_core
    from media_cli.app.details_preparation import commit_details_transition, prepare_details_transition

    source = {"_id": "one", "name": "Source", "availableEpisodes": {"sub": 0}}

    def ensure(show, ttype, **kwargs):
        show["availableEpisodes"][ttype] = 2
        show["_episode_ids"] = ["1", "2"]
        return ["1", "2"]

    monkeypatch.setattr(app_core, "ensure_episode_ids", ensure)
    monkeypatch.setattr(
        app_core, "enrich_show_if_missing", lambda show: show.update(description="Prepared", genres=["Drama"])
    )
    monkeypatch.setattr(app_core, "prepare_show_display_state", lambda *args, **kwargs: None)
    result = prepare_details_transition(source, "sub")
    assert source == {"_id": "one", "name": "Source", "availableEpisodes": {"sub": 0}}
    assert result.episode_ids == ("1", "2")
    commit_details_transition(source, result)
    assert source["description"] == "Prepared"
    assert source["availableEpisodes"]["sub"] == 2


def test_cancelled_preparation_cannot_mutate_nested_source(monkeypatch):
    import pytest

    from media_cli import app_core
    from media_cli.app.details_preparation import prepare_details_transition
    from media_cli.ui.transition import PreparationCancelled, run_preparation

    source = {"_id": "one", "availableEpisodes": {"sub": 0}}
    started, release, finished = threading.Event(), threading.Event(), threading.Event()

    def ensure(show, ttype, **kwargs):
        started.set()
        release.wait(3)
        show["availableEpisodes"][ttype] = 3
        finished.set()
        return ["1", "2", "3"]

    monkeypatch.setattr(app_core, "ensure_episode_ids", ensure)
    monkeypatch.setattr(app_core, "enrich_show_if_missing", lambda show: None)
    monkeypatch.setattr(app_core, "prepare_show_display_state", lambda *args, **kwargs: None)
    try:
        with pytest.raises(PreparationCancelled):
            run_preparation(
                lambda: prepare_details_transition(source, "sub"), tick=lambda: None, cancelled=started.is_set
            )
        release.set()
        assert finished.wait(1)
        assert source["availableEpisodes"] == {"sub": 0}
    finally:
        release.set()
