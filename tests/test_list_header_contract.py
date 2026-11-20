"""Provider placement is shared by legacy, structured and explicit list menus."""
from types import SimpleNamespace

import pytest

from media_cli.core.terminal import strip_ansi
from media_cli.ui.screen_chrome import list_header_context


@pytest.mark.parametrize('mode', ['Search', 'History', 'Downloads', 'AniList'])
@pytest.mark.parametrize('provider', ['AniPM', 'Senshi', 'AnimePahe', 'Re:ANIME'])
def test_mode_and_provider_never_repeat_beside_result_count(mode, provider):
    assert list_header_context(f'{mode} · Details', (mode, provider), badge=provider) == (
        f'{mode} · Details', provider, '')


@pytest.mark.parametrize('mode', ['Search', 'History', 'Downloads'])
def test_legacy_provider_context_uses_right_badge(mode):
    from media_cli.ui.legacy_list_picker import legacy_list_frame
    frame, _, _ = legacy_list_frame('Select mirror', ['1080p'], [0], 0,
        width=90, height=30, header=f'Title\nAlternate\nTV\nGenres\n{mode} • AniPM • Enter=select • Esc=back')
    lines = [strip_ansi(line) for line in frame.lines]
    assert lines[0].startswith(f'Anyplay · {mode} · Mirrors')
    assert lines[0].rstrip().endswith('AniPM')
    assert lines[2].strip() == '1 results'


@pytest.mark.parametrize('provider', ['AniList', 'AnimeThemes'])
def test_standalone_catalogue_context_has_provider_badge(provider):
    assert list_header_context('Songs', (provider,)) == ('Songs', provider, '')


def test_offline_and_incognito_are_not_mistaken_for_provider():
    assert list_header_context('History · Details', ('History', '[Offline]', 'AniPM', 'Incognito')) == (
        'History · Details', 'AniPM', '[Offline] · Incognito')


@pytest.mark.parametrize('mode', ['Search', 'History', 'Downloads', 'AniList'])
@pytest.mark.parametrize('child', ['Details', 'Episodes', 'Playback', 'Skip Settings'])
def test_structured_menu_children_inherit_provider_slot(monkeypatch, mode, child):
    from dataclasses import replace
    from media_cli.app.details_menu_view import build_details_menu_view
    from media_cli.ui import session_screen
    captured = {}
    monkeypatch.setattr(session_screen, 'action_picker', lambda *a, presentation, **kw: captured.update(presentation))
    show = dict(_id='one', name='Example', _provider='senshi', _provider_name='Senshi')
    view = build_details_menu_view(show, (), view_id='one', image_key='one', ttype='sub',
                                  context=(mode, 'Senshi'), show_cover=False)
    view = replace(view, screen_title=f'{mode} · {child}')
    session = SimpleNamespace(current=view, position=SimpleNamespace(active_tab_id=None))
    session_screen.session_screen_picker(SimpleNamespace(show_image=False), SimpleNamespace(tui_layout='top'),
        view.prompt, [], session=session, view_presentation=SimpleNamespace())
    assert captured['screen_title'] == f'{mode} · {child}'
    assert captured['badge'] == 'Senshi'
    assert captured['context'] == ''


@pytest.mark.parametrize('provider', ['AniList', 'AnimeThemes', 'AniPM'])
def test_query_already_in_heading_is_not_repeated_in_summary(provider):
    assert list_header_context('Results · oshi no ko', (provider, '"oshi no ko"'), badge=provider) == (
        'Results · oshi no ko', provider, '')


@pytest.mark.parametrize('invalid', [None, 42, 'bad\x1b[31m'])
def test_explicit_provider_badge_validates_like_header_title(invalid):
    from media_cli.ui.view_model import ViewSpec
    with pytest.raises((TypeError, ValueError)):
        ViewSpec('test', 'Menu', (), screen_badge=invalid)
