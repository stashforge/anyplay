import pytest
from media_cli.domain import history


@pytest.mark.parametrize('outcome', ['_provider_catalog_checked_at', '_provider_catalog_attempted_at', '_provider_catalog_failed_at', '_anilist_airing_attempted_at'])
def test_all_attempt_outcomes_wait_one_hour(outcome, monkeypatch):
    monkeypatch.setattr(history, 'HISTORY_REFRESH_COOLDOWN_SECONDS', 3600)
    entry = {'show': {outcome: 10000}}
    assert not history.history_entry_refresh_is_stale(entry, now=10061)
    assert not history.history_entry_refresh_is_stale(entry, now=13599)
    assert history.history_entry_refresh_is_stale(entry, now=13600)


def test_shared_setting_and_minimum(monkeypatch):
    monkeypatch.setattr(history, 'HISTORY_REFRESH_COOLDOWN_SECONDS', 3600)
    history.configure_history_refresh({'history_refresh_minutes': 30})
    assert history.HISTORY_REFRESH_COOLDOWN_SECONDS == 1800
    for value in (29, 0, -1):
        history.configure_history_refresh({'history_refresh_minutes': value})
        assert history.HISTORY_REFRESH_COOLDOWN_SECONDS == 1800
    for value in (True, 30.5, '', None, 'invalid'):
        history.configure_history_refresh({'history_refresh_minutes': value})
        assert history.HISTORY_REFRESH_COOLDOWN_SECONDS == 3600
    history.configure_history_refresh({})
    assert history.HISTORY_REFRESH_COOLDOWN_SECONDS == 3600


def test_latest_attempt_wins_without_changing_success_time():
    show = {'_provider_catalog_checked_at': 9000, '_provider_catalog_failed_at': 10000}
    assert history.history_refresh_last_checked_at(show) == 10000
    assert show['_provider_catalog_checked_at'] == 9000


def test_failed_anilist_attempt_is_persisted_and_not_repeated(monkeypatch):
    from media_cli.core import anilist
    entry = {'show': {'_id': 'one', 'aniListId': '42', 'name': 'Example',
                     'status': 'RELEASING', 'description': 'Known', 'genres': ['Drama'],
                     'thumbnail': 'known'}, 'episode': '1'}
    calls, saved = [], []
    monkeypatch.setattr(anilist._time, 'time', lambda: 10000)
    monkeypatch.setattr(anilist, '_provider_catalog_refresh_fn', None)
    def fail(*a, **kw):
        calls.append(True)
        raise OSError('Offline')
    monkeypatch.setattr(anilist.anilist_service, 'fetch', fail)
    monkeypatch.setattr(anilist.storage, 'patch_history_entry_state', lambda entry: saved.append(entry))
    assert anilist.refresh_history_anilist_airing_batch([entry])
    assert entry['show']['_anilist_airing_attempted_at'] == 10000
    assert '_anilist_airing_checked_at' not in entry['show']
    assert saved
    assert not anilist.refresh_history_anilist_airing_batch([entry])
    assert len(calls) == 1
    assert anilist.refresh_history_anilist_airing_batch([entry], force=True)
    assert len(calls) == 2


def test_config_serializes_one_shared_interval():
    from media_cli.state.config import DEFAULT_CONFIG, serialize_conf, parse_conf_text
    assert DEFAULT_CONFIG['history_refresh_minutes'] == 60
    assert parse_conf_text(serialize_conf({'history_refresh_minutes': 90}))['history_refresh_minutes'] == 90
