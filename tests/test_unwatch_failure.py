"""Unwatch failures must retain resume and must not display false success."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from test_provider_details_recovery import recovery  # noqa: F401

from media_cli import app_core
from media_cli.app import playback_menu
from media_cli.app.details_menu_controller import DetailsMenuResult


@pytest.fixture
def unwatch(recovery, monkeypatch):  # noqa: F811 - reuse the existing pytest fixture
    r = recovery
    r.show.update(_episode_ids=['1', '2', '3'], _episode_ids_ttype='sub',
                  _episode_catalog_state='loaded', _local_progress=2,
                  _anilist_progress=2, watched_episodes=['1', '2'], episodeCount=3)
    r.ms.current_ep, r.ms.current_ep_index = '2', 1
    r.flags.incognito_mode = False
    r.args.sync, r.args.no_sync = True, False
    monkeypatch.setattr(app_core, 'ensure_episode_ids', lambda *a, **kw: ['1', '2', '3'])
    monkeypatch.setattr(app_core, 'get_local_progress', lambda *a: 2)
    monkeypatch.setattr(app_core, 'get_show_anilist_id', lambda *a: 101)
    monkeypatch.setattr(playback_menu, 'run_details_menu', lambda *a, **kw: DetailsMenuResult('selected', 'UNTRACK'))
    feedback = []
    monkeypatch.setattr(app_core, 'set_action_feedback', lambda show, message: feedback.append(message))
    resume = Mock()
    sync = Mock(return_value=False)
    write = Mock(return_value={'episode': '1'})
    monkeypatch.setattr(app_core, 'save_resume_time', resume)
    monkeypatch.setattr(app_core, 'sync_watched_to_anilist', sync)
    monkeypatch.setattr(app_core, 'write_history_progress', write)
    def run(tracking=True):
        return playback_menu.handle_action_menu_state(r.flags, r.ui, r.ms,
            {'anilist_token': 'fixture-token'} if tracking else {}, r.args, 'sub', lambda *a: tracking)
    return SimpleNamespace(r=r, run=run, feedback=feedback, resume=resume, sync=sync, write=write)


def test_failed_anilist_unwatch_preserves_resume_and_reports_failure(unwatch):
    c = unwatch
    assert c.run() == 'DETAILS'
    c.sync.assert_called_once()
    c.resume.assert_not_called()
    c.write.assert_not_called()
    assert c.r.ms.current_ep == '2'
    assert c.r.show['_local_progress'] == 2 and c.r.show['_anilist_progress'] == 2
    assert any('failed' in message.lower() for message in c.feedback)
    assert not any('✔' in message for message in c.feedback)


def test_successful_anilist_unwatch_still_clears_resume(unwatch):
    c = unwatch
    c.sync.return_value = True
    assert c.run() == 'DETAILS'
    c.resume.assert_called_once()
    assert c.r.ms.current_ep == '2'
    assert any('Marked' in message and 'unwatched' in message for message in c.feedback)


def test_failed_local_progress_update_keeps_resume(unwatch):
    c = unwatch
    c.write.return_value = None
    assert c.run(tracking=False) == 'DETAILS'
    c.resume.assert_not_called()
    assert any('failed' in message.lower() for message in c.feedback)
    assert not any('✔' in message for message in c.feedback)


def test_successful_local_unwatch_still_clears_resume(unwatch):
    c = unwatch
    assert c.run(tracking=False) == 'DETAILS'
    c.write.assert_called_once()
    c.resume.assert_called_once()


def test_download_local_unwatch_survives_failed_sync_without_false_synced_message(unwatch, monkeypatch):
    c = unwatch
    c.r.ms._is_downloads = True
    c.r.show['_folder_name'] = 'Example'
    def record(show, *a, **kw):
        show['watched_episodes'] = ['1']
        return True
    monkeypatch.setattr(app_core, 'record_download_unwatch_progress', record)
    assert c.run() == 'DETAILS'
    assert c.r.show['watched_episodes'] == ['1']
    c.resume.assert_not_called()
    assert any('locally' in message.lower() and 'failed' in message.lower() for message in c.feedback)
    assert not any('✔' in message for message in c.feedback)
