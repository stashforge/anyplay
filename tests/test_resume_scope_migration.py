"""Activate scoped resumes only with safe legacy ownership and callback context."""
from copy import deepcopy

import pytest
from media_cli.core import storage
from media_cli.playback import engine


@pytest.fixture
def saved(monkeypatch):
    prefs = {'same': {'resumes': {'1': 45}, 'pending_completion': {'episode': '1', 'next_episode': '2'}}}
    history = [{'show': {'_id': 'same', '_provider': 'anipm'}}]
    monkeypatch.setattr(storage, 'load_prefs', lambda: prefs)
    monkeypatch.setattr(storage, 'save_prefs', lambda value: None)
    monkeypatch.setattr(storage, 'load_history', lambda: history)
    return prefs, history


def test_unique_legacy_owner_keeps_resume_and_delayed_completion(saved):
    prefs, history = saved
    assert storage.get_resume_time('same', '1', 'anipm') == 45
    assert storage.get_pending_completion('same', 'anipm')['episode'] == '1'
    assert storage.get_resume_time('same', '1', 'senshi') == 0
    assert prefs['same']['resumes']['1'] == 45
    history[:] = [{'show': {'_id': 'same', '_provider': 'senshi'}}]
    assert storage.get_resume_time('same', '1', 'senshi') == 0


def test_ambiguous_legacy_owner_is_not_guessed(saved):
    prefs, history = saved
    history.append({'show': {'_id': 'same', '_provider': 'senshi'}})
    original = deepcopy(prefs)
    assert storage.get_resume_time('same', '1', 'anipm') == 0
    assert storage.get_resume_time('same', '1', 'senshi') == 0
    assert prefs == original


def test_clearing_scoped_resume_cannot_resurrect_legacy_position(saved):
    storage.save_resume_time('same', '1', 0, 'anipm')
    assert storage.get_resume_time('same', '1', 'anipm') == 0
    assert storage.get_resume_time('same', '1') == 45


def test_pending_completion_clear_stays_with_its_provider(saved):
    storage.save_pending_completion('same', '1', 1, '2', 50, 100, 'anipm')
    storage.save_pending_completion('same', '3', 3, '4', 80, 100, 'senshi')
    storage.clear_pending_completion('same', 'anipm')
    assert storage.get_pending_completion('same', 'anipm') == {}
    assert storage.get_pending_completion('same', 'senshi')['episode'] == '3'


def test_desktop_resume_reader_captures_original_provider(monkeypatch):
    calls = []
    monkeypatch.setitem(engine._player_ui_state, 'show', {'_provider': 'anipm'})
    monkeypatch.setattr(engine, 'get_resume_time', lambda sid, ep, pid: calls.append((sid, ep, pid)) or 45)
    def player(*args, **kwargs):
        engine._player_ui_state['show'] = {'_provider': 'senshi'}
        assert kwargs['get_resume_time']('same', '1') == 45
        return 'played'
    monkeypatch.setattr(engine.desktop_playback, 'play_desktop', player)
    assert engine.play_desktop('Example', '1', {}, show_id='same') == 'played'
    assert calls == [('same', '1', 'anipm')]


def test_history_entry_resume_selection_uses_its_own_provider(saved):
    from media_cli.domain.history import playback_ep_from_history_entry
    storage.save_resume_time('same', '1', 45, 'anipm')
    for provider, expected in [('anipm', '1'), ('senshi', '2')]:
        entry = {'show': {'_id': 'same', '_provider': provider}, 'episode': '1'}
        assert playback_ep_from_history_entry(entry, episode_ids=['1', '2']) == expected


@pytest.mark.parametrize('legacy_record', [False, True])
def test_queued_completion_only_clears_own_provider_resume(saved, legacy_record):
    from media_cli.services.anilist_queue import _finish_queued_pending_completion
    storage.save_resume_time('same', '1', 45, 'anipm')
    storage.save_resume_time('same', '1', 90, 'senshi')
    storage.save_pending_completion('same', '1', 1, '2', 45, 100, 'anipm')
    storage.save_pending_completion('same', '1', 1, '2', 90, 100, 'senshi')
    completion = {'show_id': 'same', 'episode': '1'}
    if not legacy_record:
        completion['provider_id'] = 'anipm'
    _finish_queued_pending_completion({'pending_completion': completion})
    assert storage.get_resume_time('same', '1', 'anipm') == 0
    assert storage.get_pending_completion('same', 'anipm') == {}
    assert storage.get_resume_time('same', '1', 'senshi') == 90
    assert storage.get_pending_completion('same', 'senshi')['episode'] == '1'
