import pytest
from media_cli.cli.args import parse_cli_args
from media_cli.providers import available_providers


def test_cli_search_with_download_flag():
    args, _ = parse_cli_args(["search", "slime", "-e", "3", "--download"])
    assert args.command == "search"
    assert args.query == ["slime"]
    assert args.episode == "3"
    assert args.download is True


def test_cli_search_with_download_range():
    args, _ = parse_cli_args(["search", "slime", "-e", "1-12", "--download"])
    assert args.command == "search"
    assert args.query == ["slime"]
    assert args.episode == "1-12"
    assert args.download is True


def test_cli_search_with_download_and_sub_langs():
    args, _ = parse_cli_args([
        "search", "slime", "-e", "1",
        "--download",
        "--sub-langs", "eng,jpn",
        "--audio-langs", "jpn",
    ])
    assert args.download is True
    assert args.download_sub_langs == "eng,jpn"
    assert args.download_audio_langs == "jpn"


def test_cli_sidecar_subs_option_is_removed():
    with pytest.raises(SystemExit):
        parse_cli_args([
            "search", "slime", "-e", "1",
            "--download",
            "--sidecar-subs",
        ])


def test_cli_provider_search_with_download_flag():
    args, _ = parse_cli_args(["search", "anikoto:slime", "-e", "2", "--download"])
    assert args.command == "search"
    assert args.provider == "anikoto"
    assert args.query == ["slime"]
    assert args.episode == "2"
    assert args.download is True


def test_cli_provider_requires_search_subcommand():
    with pytest.raises(SystemExit):
        parse_cli_args(["anikoto", "slime"])


@pytest.mark.parametrize("query", [["animexin:renegade immortal"], ["animexin:renegade", "immortal"]])
def test_provider_prefix_accepts_multiword_query(query):
    args, _ = parse_cli_args(["search", *query])
    assert args.provider == "animexin"
    assert " ".join(args.query) == "renegade immortal"


def test_provider_prefix_after_options():
    args, _ = parse_cli_args(["search", "-q", "720p", "animexin:renegade", "immortal"])
    assert args.provider == "animexin"
    assert args.query == ["renegade", "immortal"]


def test_provider_prefix_with_global_plain_flag():
    args, _ = parse_cli_args(["--plain", "search", "animexin:renegade"])
    assert args.provider == "animexin"
    assert args.query == ["renegade"]


@pytest.mark.parametrize("argv", [
    ["search", "animxin:slime"],
    ["search", "animexin:slime", "--provider", "miruro"],
    ["animexin", "search", "slime"],
])
def test_invalid_provider_syntax(argv):
    with pytest.raises(SystemExit) as error:
        parse_cli_args(argv)
    assert error.value.code == 2


def test_colon_after_first_query_word_is_preserved():
    args, _ = parse_cli_args(["search", "title", "part:two"])
    assert args.query == ["title", "part:two"]


@pytest.mark.parametrize("provider", ["animexin", "animethemes"])
def test_provider_specific_help(provider, capsys):
    with pytest.raises(SystemExit) as error:
        parse_cli_args(["search", provider + ":", "-h"])
    assert error.value.code == 0
    assert f"search {provider}:<query>" in capsys.readouterr().out


@pytest.mark.parametrize("provider", sorted(available_providers()))
def test_every_episode_provider_supports_prefix(provider):
    args, _ = parse_cli_args(["search", provider + ":example", "title"])
    assert args.provider == provider
    assert args.query == ["example", "title"]


@pytest.mark.parametrize("provider", ["animexin", "animethemes", "torrent"])
def test_provider_first_error_suggests_search_prefix(provider, capsys):
    with pytest.raises(SystemExit) as error:
        parse_cli_args([provider, "search", "renegade", "immortal", "-p", "mpv"])
    assert error.value.code == 2
    output = capsys.readouterr().out
    assert f"anyplay search {provider}:renegade immortal -p mpv" in output
    assert "  completion\n" not in output


def test_provider_first_suggestion_preserves_quoted_query(capsys):
    import shlex
    with pytest.raises(SystemExit):
        parse_cli_args(["animexin", "search", "renegade immortal"])
    output = capsys.readouterr().out
    suggestion = output.split("Did you mean:\n  ")[1].splitlines()[0]
    args, _ = parse_cli_args(shlex.split(suggestion)[1:])
    assert args.provider == "animexin"
    assert args.query == ["renegade immortal"]
