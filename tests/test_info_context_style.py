from media_cli.ui.library_info import context_content, finish_info_panel
from media_cli.core.terminal import strip_ansi


def test_context_labels_values_and_spacing():
    content = context_content('AnimeXin · Updated 3h ago · Last activity 1d ago • 3 downloaded')
    assert ''.join(run.text for run in content.runs) == 'AnimeXin  Updated 3h ago  Last activity 1d ago  3 downloaded'
    assert {run.text.strip() for run in content.runs if run.role == 'library.label'} == {'Updated', 'Last activity', 'downloaded'}
    assert all(run.role in {'library.label', 'secondary', 'library.separator'} for run in content.runs)


def test_shared_panel_retains_genre_bullets_and_fifth_slot():
    lines = finish_info_panel(('Title', 'Alt', 'Metadata'), 'Martial Arts • Slice of Life',
                              'AniPM · Updated 1d ago · 2 downloaded', 150)
    assert strip_ansi(lines[3]) == 'Martial Arts • Slice of Life'
    assert strip_ansi(lines[4]) == 'AniPM  Updated 1d ago  2 downloaded'
