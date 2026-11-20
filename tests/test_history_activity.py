from media_cli.app.history_activity import history_activity


def test_resume_and_downloads_are_provider_scoped_and_real_files_only(tmp_path):
    local = tmp_path / 'episode.mkv'
    local.write_bytes(b'video')
    entries = [dict(show=dict(_id='same', _provider=provider), episode='12',
                    translation_type='sub', timestamp=100) for provider in ('anipm', 'senshi')]
    preferences = {'provider:anipm:same': {'resumes': {'12': 522}}}
    downloads = {'shows': {'folder': {'metadata': entries[0]['show'], 'episode_files': {
        '11': [str(local)], '12': [str(tmp_path / 'missing.mkv')]}}}}
    lines = history_activity(entries, preferences, downloads, now=3700)
    assert lines[('anipm', 'same', 'sub')] == 'Continue EP 12 · 08:42 · 1 downloaded'
    assert lines[('senshi', 'same', 'sub')] == 'Last activity 1h ago'


def test_fifth_activity_line_is_projected_without_file_reads():
    from media_cli.app.history_menu_view import build_history_items
    entry = dict(show=dict(_id='one', _provider='anipm', name='Title', genres=['Comedy']), episode='1')
    items = build_history_items([entry], show_cover=False,
        activity={('anipm', 'one', 'sub'): 'Continue EP 1 · 02:00'})
    assert items[0].activity == 'Continue EP 1 · 02:00'
    assert items[0].extra_details == 'Comedy'


def test_fifth_line_fits_tall_screen_and_collapses_on_small_screen():
    from media_cli.ui.list_screen import list_screen_frame
    from media_cli.ui.list_selection import ListSelection
    state = ListSelection([('one', {'name': 'Title'})], '')
    info = ('Title', 'Alt', 'Metadata', 'Provider', 'Last activity 1h ago')
    tall = list_screen_frame(state, 'History', width=80, height=40, info=info)
    small = list_screen_frame(state, 'History', width=40, height=24, info=info)
    assert any('Last activity' in line for line in tall.lines)
    assert not any('Last activity' in line for line in small.lines)
    assert tall.lines[-3] == ''
