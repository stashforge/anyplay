from media_cli.ui.torrent_results import information_content
from media_cli.ui.library_info import library_info_lines


def show(progress=None):
    record = {'_id': 'a' * 40, '_provider': 'torrent', 'name': 'Example.S01.mkv',
              '_torrent': {'name': 'Example.S01.mkv', 'seeders': 22, 'leechers': 3,
                           'size_bytes': 1000000000, 'source': 'nyaa'}}
    if progress is not None:
        record['_local_progress'] = progress
    return record


def plain(lines):
    return '\n'.join(''.join(run.text for run in line.runs) for line in lines)


def test_prepared_details_progress_uses_same_compact_stats_as_history():
    text = plain(information_content(show(4)))
    assert 'Watched 4' in text
    assert 'S 22 · L 3' in text


def test_search_panel_reads_current_saved_progress_for_requested_translation(monkeypatch):
    record = show(99)
    seen = []
    def progress(value, translation):
        seen.append((value['_id'], translation))
        return 2
    monkeypatch.setattr('media_cli.core.storage.get_local_progress', progress)
    text = '\n'.join(library_info_lines(record, 'dub', 120))
    assert 'Watched ' in text and '2' in text
    assert seen == [('a' * 40, 'dub')]
    assert record['_local_progress'] == 99


def test_unknown_progress_does_not_invent_watched_count():
    assert 'Watched' not in plain(information_content(show()))
