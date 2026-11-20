import pytest
from media_cli.cli.args import parse_cli_args


@pytest.mark.parametrize('argv', [
    ['--incognito'], ['--incognito', 'search', 'slime'],
    ['search', 'slime', '--incognito'], ['--incognito', 'history'],
    ['history', '--incognito'], ['--incognito', 'anilist', 'search', 'slime'],
    ['anilist', 'search', 'slime', '--incognito'],
    ['providers', '--incognito'], ['--incognito', 'service', 'status'],
])
def test_global_incognito(argv):
    args, _ = parse_cli_args(argv)
    assert args.incognito is True


def test_incognito_literal_query_after_separator():
    args, _ = parse_cli_args(['search', '--', '--incognito'])
    assert not args.incognito



def test_private_home_keeps_history_action():
    from media_cli.ui.search_home import SearchHomeState
    state = SearchHomeState()
    assert any(item[0] == "history" for item in state.menu)
