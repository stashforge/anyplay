from media_cli.services.history_notifications import check_anilist, observe_release, scheduled_alerts


def entry(count, progress=10):
    return {'progress': progress, 'show': {
        '_id': 'x', '_provider': 'anipm', '_anilist_id': 123, 'name': 'Example',
        '_episode_catalog_state': 'loaded', '_provider_catalog_checked_at': 100,
        'availableEpisodes': {'sub': count}}}


def test_provider_release_baseline_catchup_and_dedup():
    state, sent = {}, []
    send = lambda *args: (sent.append(args) or True)
    observe_release(state, entry(10), send=send)
    assert not sent
    observe_release(state, entry(11), send=send)
    observe_release(state, entry(11), send=send)
    assert len(sent) == 1
    assert sent[0][0] == 'New episode available'
    observe_release(state, entry(12, progress=3), send=send)
    assert len(sent) == 2


def test_schedule_due_once_not_future_or_stale():
    state = {'schedules': {'al:123': {'at': 200, 'episode': 2, 'name': 'Example'}}}
    sent = []
    send = lambda *args: (sent.append(args) or True)
    scheduled_alerts(state, 199, send=send)
    assert not sent
    scheduled_alerts(state, 201, send=send)
    scheduled_alerts(state, 202, send=send)
    assert len(sent) == 1
    assert sent[0][0] == 'Scheduled to air'
    assert 'AniList schedule' in sent[0][1]
    scheduled_alerts({'schedules': state['schedules']}, 4000, send=send)
    assert len(sent) == 1


def test_anilist_lists_cooldown_and_local_dedup(monkeypatch):
    from media_cli.core import anilist, storage
    from media_cli.domain import history
    calls, refreshes, sent = [], [], []
    def fetch(token, status, **kwargs):
        calls.append(status)
        return [{'_id': '123', 'name': 'Example', '_next_airing_at': 101,
                 '_next_airing_ep': 11, '_anilist_progress': 10}]
    monkeypatch.setattr(anilist, 'fetch_anilist_list', fetch)
    monkeypatch.setattr(storage, 'get_al_match', lambda aid: {'_id': 'x', '_provider': 'anipm'})
    monkeypatch.setattr(history, 'refresh_history_entry_provider_catalog', lambda *a, **k: refreshes.append(a))
    state = {}
    config = {'anilist_token': 'test', 'history_refresh_minutes': 60}
    send = lambda *args: (sent.append(args) or True)
    check_anilist(state, config, [entry(10)], now=10000, send=send)
    assert calls == ['CURRENT', 'REPEATING', 'PLANNING']
    assert not refreshes
    check_anilist(state, config, [entry(10)], now=10001, send=send)
    assert len(calls) == 3


def test_unconfirmed_catalogue_never_notifies():
    state, sent = {}, []
    row = entry(10)
    row['show'].pop('_provider_catalog_checked_at')
    observe_release(state, row, send=lambda *args: sent.append(args))
    assert not state and not sent


def test_linked_titles_checked_without_adding_to_history(monkeypatch):
    from media_cli.core import anilist, storage
    from media_cli.domain import history
    calls = []
    monkeypatch.setattr(anilist, 'fetch_anilist_list', lambda *a, **k: [{'_id': '123', 'name': 'Example', '_anilist_progress': 10}])
    monkeypatch.setattr(storage, 'get_al_match', lambda aid: {'_id': 'x', '_provider': 'anipm'})
    def refresh(row, **kwargs):
        calls.append(row)
        row['show'].update(entry(10)['show'])
        return True
    monkeypatch.setattr(history, 'refresh_history_entry_provider_catalog', refresh)
    monkeypatch.setattr(storage, 'patch_history_entry_state', lambda row: (_ for _ in ()).throw(AssertionError('Do not insert tracker entries')))
    state = {}
    check_anilist(state, {'anilist_token': 'test'}, [], now=10000)
    assert len(calls) == 1  # Same ID in both lists is not checked twice.
    assert 'al:123' in state['linked']


def test_failed_notification_is_not_marked_delivered():
    state = {'schedules': {'al:1': {'at': 100, 'episode': 1, 'name': 'Example'}}}
    scheduled_alerts(state, 101, send=lambda *args: False)
    assert not state['airing_notified']


def test_saved_cli_observation_notifies_and_deduplicates():
    row = entry(35, progress=34)
    row['show'].pop('_episode_catalog_state')
    row['show']['_release_notification'] = {'previous': 34, 'available': 35, 'translation': 'sub'}
    state, sent = {}, []
    send = lambda *args: (sent.append(args) or True)
    observe_release(state, row, send=send)
    observe_release(state, row, send=send)
    assert len(sent) == 1
    assert not state['pending_releases']


def test_failed_release_delivery_survives_serialization_and_retries():
    import json
    state = {}
    observe_release(state, entry(10), send=lambda *args: True)
    observe_release(state, entry(11), send=lambda *args: False)
    assert state['pending_releases']
    state = json.loads(json.dumps(state))
    sent = []
    observe_release(state, entry(11), send=lambda *args: (sent.append(args) or True))
    assert len(sent) == 1 and not state['pending_releases']


def test_same_anilist_release_deduplicates_across_providers():
    row = entry(11)
    row['show']['_release_notification'] = {'previous': 10, 'available': 11, 'translation': 'sub'}
    state, sent = {}, []
    send = lambda *args: (sent.append(args) or True)
    observe_release(state, row, send=send)
    row['show']['_provider'] = 'other'
    observe_release(state, row, send=send)
    assert len(sent) == 1


def test_refresh_event_survives_storage_and_service_observation(monkeypatch):
    from types import SimpleNamespace
    from media_cli.domain import history
    from media_cli.core.storage import sanitize_show_for_history
    row = entry(34, progress=34)
    row["episode"] = "34"
    # Metadata may already contain the new count before catalogue parsing.
    provider = SimpleNamespace(
        get_title=lambda *a: {'availableEpisodes': {'sub': 35}},
        episode_catalog=lambda *a, **k: {'state': 'loaded', 'ids': [str(i) for i in range(1, 36)], 'labels': {}},
    )
    monkeypatch.setattr('media_cli.services.catalog._provider_for_title', lambda *a: provider)
    assert history.refresh_history_entry_provider_catalog(row, enrich=False)
    row['show'] = sanitize_show_for_history(row['show'])
    assert '_episode_catalog_state' not in row['show']
    assert row['show']['_release_notification']['previous'] == 34
    sent = []
    observe_release({}, row, send=lambda *a: (sent.append(a) or True))
    assert len(sent) == 1


def test_batch_releases_one_popup_and_planning_only_first():
    from media_cli.services.history_notifications import deliver_releases
    state, sent = {}, []
    send = lambda *a: (sent.append(a) or True)
    row = entry(0, progress=0)
    observe_release(state, row, defer=True, first_only=True)
    row['show']['availableEpisodes']['sub'] = 1
    observe_release(state, row, defer=True, first_only=True)
    other = entry(12, progress=1)
    other['show']['_id'] = 'other'
    other['show']['_anilist_id'] = 456
    other['show']['_release_notification'] = {'previous': 11, 'available': 12, 'translation': 'sub'}
    observe_release(state, other, defer=True)
    deliver_releases(state, send=send)
    assert len(sent) == 1 and '2 shows' in sent[0][0]
    row['show']['availableEpisodes']['sub'] = 2
    observe_release(state, row, send=send, first_only=True)
    assert len(sent) == 1


def test_mark_near_caught_up_and_acknowledgement(tmp_path, monkeypatch):
    from media_cli.domain.history import history_entry_category
    from media_cli.core import storage
    row = entry(35, progress=31)
    row['episode'] = '31'
    row['show']['_release_notification'] = {'previous': 34, 'available': 35, 'translation': 'sub', 'progress': 31}
    history_entry_category(row)
    assert row['has_new_release']
    row['show']['_release_seen'] = {'sub': 35}
    history_entry_category(row)
    assert not row['has_new_release']
    row['show']['_release_notification'] = {'previous': 35, 'available': 36, 'translation': 'sub', 'progress': 30}
    row['show']['availableEpisodes']['sub'] = 36
    history_entry_category(row)
    assert not row['has_new_release']


def test_old_sticky_mark_without_release_evidence_clears():
    from media_cli.domain.history import history_entry_category
    row = entry(35, progress=34)
    row['episode'] = '34'
    row['has_new_release'] = True
    history_entry_category(row)
    assert not row['has_new_release']


def test_open_details_ack_is_persisted_without_progress_change(monkeypatch):
    from copy import deepcopy
    from media_cli.core import storage
    row = entry(35, progress=34)
    row['episode'] = '34'
    row['show']['_release_notification'] = {'previous': 34, 'available': 35, 'translation': 'sub', 'progress': 34}
    monkeypatch.setattr(storage, 'is_incognito', lambda: False)
    monkeypatch.setattr(storage, 'load_history', lambda: [row])
    saved = []
    monkeypatch.setattr(storage, 'save_refreshed_history', lambda rows: saved.append(deepcopy(rows)))
    opened = deepcopy(row['show'])
    storage.acknowledge_history_release(opened, 'sub')
    assert saved[-1][0]['progress'] == 34
    assert saved[-1][0]['show']['_release_seen'] == {'sub': 35}
    assert not saved[-1][0]['has_new_release']
    # A new catalogue can mark again; acknowledgement only covers the old count.
    row['show']['availableEpisodes']['sub'] = 36
    row['show']['_release_notification'] = {'previous': 35, 'available': 36, 'translation': 'sub', 'progress': 34}
    from media_cli.domain.history import history_entry_category
    history_entry_category(row)
    assert row['has_new_release']


def test_planning_catalogue_stops_after_first_release(monkeypatch):
    from media_cli.core import anilist, storage
    from media_cli.domain import history
    monkeypatch.setattr(anilist, 'fetch_anilist_list', lambda token, status, **k:
                        [{'_id': '123', 'name': 'Planned'}] if status == 'PLANNING' else [])
    monkeypatch.setattr(storage, 'get_al_match', lambda aid: {'_id': 'x', '_provider': 'anipm'})
    calls = []
    def refresh(row, **kwargs):
        count = len(calls)
        calls.append(count)
        row['show'].update(entry(count)['show'])
        return True
    monkeypatch.setattr(history, 'refresh_history_entry_provider_catalog', refresh)
    state, sent = {}, []
    send = lambda *a: (sent.append(a) or True)
    for now in (10000, 14000, 18000):
        check_anilist(state, {'anilist_token': 'test'}, [], now=now, send=send)
    assert len(calls) == 2
    assert len(sent) == 1 and 'Episode 1' in sent[0][1]


def test_manual_recovery_of_missed_release_only_once():
    row = entry(35, progress=34)
    row['was_caught_up'] = True
    state = {'releases': {'al:123': 35}}
    sent = []
    send = lambda *a: (sent.append(a) or True)
    observe_release(state, row, send=send)
    assert not sent
    observe_release(state, row, send=send, recover=True)
    observe_release(state, row, send=send, recover=True)
    assert len(sent) == 1
    row['show']['availableEpisodes']['sub'] = 100
    observe_release({'releases': {'al:123': 100}}, row, send=send, recover=True)
    assert len(sent) == 1


def test_desktop_identity_and_notification_id(monkeypatch, tmp_path, caplog):
    import logging
    from types import SimpleNamespace
    from media_cli.services import history_notifications as notifications
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path))
    monkeypatch.setattr(notifications.shutil, 'which', lambda _: '/usr/bin/notify-send')
    calls = []
    monkeypatch.setattr(notifications.subprocess, 'run', lambda command, **kw:
                        (calls.append(command) or SimpleNamespace(returncode=0, stdout='315\n', stderr='')))
    with caplog.at_level(logging.INFO):
        assert notifications.notify('Release', 'Example episode 35')
    desktop = tmp_path / 'applications/org.anyplay.CLI.desktop'
    assert 'NoDisplay=true' in desktop.read_text()
    assert '--hint=string:desktop-entry:org.anyplay.CLI' in calls[0]
    assert '--urgency=normal' in calls[0]
    assert 'id=315' in caplog.text
    desktop.write_text('custom existing entry')
    assert notifications.notification_desktop_entry() == 'org.anyplay.CLI'
    assert desktop.read_text() == 'custom existing entry'


def test_manual_recovery_sets_mark_but_preserves_acknowledgement():
    from media_cli.domain.history import history_entry_category
    row = entry(35, progress=34)
    row['episode'] = '34'
    row['was_caught_up'] = True
    observe_release({}, row, send=lambda *a: True, recover=True)
    history_entry_category(row)
    assert row['has_new_release']
    row['show']['_release_seen'] = {'sub': 35}
    observe_release({}, row, send=lambda *a: True, recover=True)
    history_entry_category(row)
    assert not row['has_new_release']
