import pytest
from media_cli.cli.args import parse_cli_args


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
    args, _ = parse_cli_args(["anikoto", "search", "slime", "-e", "2", "--download"])
    assert args.command == "search"
    assert args.provider == "anikoto"
    assert args.query == ["slime"]
    assert args.episode == "2"
    assert args.download is True


def test_cli_provider_requires_search_subcommand():
    with pytest.raises(SystemExit):
        parse_cli_args(["anikoto", "slime"])
