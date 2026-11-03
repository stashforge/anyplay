"""Batch display must use the same identity rules as individual progress reads."""
import pytest

from media_cli.context import FLAGS
from media_cli.domain.metadata import batch_prepare_shows_display_state


@pytest.fixture
def history(monkeypatch):
    entries = []
    monkeypatch.setattr(FLAGS, 'incognito_mode', False)
    monkeypatch.setattr(FLAGS, 'sync_force_on', False)
    monkeypatch.setattr(FLAGS, 'sync_force_off', False)
    monkeypatch.setattr('media_cli.core.storage.load_history', lambda: entries)
    monkeypatch.setattr('media_cli.core.storage.load_prefs', lambda: {})
    return entries


@pytest.mark.parametrize('reverse', [False, True])
@pytest.mark.parametrize('ttype', ['sub', 'dub'])
def test_provider_local_id_collision_keeps_each_titles_progress_and_metadata(history, reverse, ttype):
    rows = [
        {'show': {'_id': 'same-id', '_provider': 'anipm', 'name': 'Alpha', 'genres': ['Drama']}, 'episode': '3', 'translation_type': ttype},
        {'show': {'_id': 'same-id', '_provider': 'senshi', 'name': 'Zeta', 'genres': ['Comedy']}, 'episode': '8', 'translation_type': ttype},
    ]
    history.extend(reversed(rows) if reverse else rows)
    shows = [{k: row['show'][k] for k in ('_id', '_provider', 'name')} for row in rows]
    batch_prepare_shows_display_state(shows, ttype)
    assert [show['_local_progress'] for show in shows] == [3, 8]
    assert [show['_local_episode_label'] for show in shows] == ['3', '8']
    assert [show['genres'] for show in shows] == [['Drama'], ['Comedy']]


def test_name_index_cannot_override_conflicting_tracker_identity(history):
    history.append({'show': {'_id': 'other', '_provider': 'senshi', 'name': 'Example', 'aniListId': '222', 'genres': ['Wrong']},
                    'episode': '7', 'translation_type': 'sub'})
    show = {'_id': 'one', '_provider': 'anipm', 'name': 'Example', 'aniListId': '111'}
    batch_prepare_shows_display_state([show])
    assert show['_local_progress'] is None
    assert show.get('_local_episode_label') != '7'
    assert 'genres' not in show


def test_real_tracker_match_still_shares_progress_across_providers(history):
    history.append({'show': {'_id': 'other', '_provider': 'senshi', 'name': 'Zeta', 'aniListId': '111'},
                    'episode': '4', 'translation_type': 'sub'})
    show = {'_id': 'one', '_provider': 'anipm', 'name': 'Alpha', 'aniListId': '111'}
    batch_prepare_shows_display_state([show])
    assert show['_local_progress'] == 4
