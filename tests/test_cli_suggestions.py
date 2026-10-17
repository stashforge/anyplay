import pytest

from media_cli.cli.args import parse_cli_args
from media_cli.cli.suggestions import suggest


@pytest.mark.parametrize("value,choices,expected", [
    ("serach", ["search", "auth", "providers"], ["search"]),
    ("mvp", ["mpv", "mpvrx", "mpvrex"], ["mpv"]),
    ("watcing", ["watching", "planning"], ["watching"]),
    ("sett", ["set", "get"], ["set"]),
    ("zzzz", ["search", "history"], []),
    ("s", ["search"], []),
    ("histroy", ["history", "anilist"], ["history"]),
])
def test_conservative_suggestions(value, choices, expected):
    assert suggest(value, choices) == expected


@pytest.mark.parametrize("argv,label,correction", [
    (["serach"], "Unknown command: serach", "anyplay search"),
    (["config", "sett"], "Unknown action: sett", "anyplay config set"),
    (["auth", "logni"], "Unknown action: logni", "anyplay auth login"),
    (["anilist", "watcing"], "Unknown AniList list: watcing", "anyplay anilist watching"),
    (["search", "slime", "-p", "mvp"], "Invalid player: mvp", "mpv"),
])
def test_errors_use_contextual_suggestions(argv, label, correction, capsys):
    with pytest.raises(SystemExit) as error:
        parse_cli_args(argv)
    assert error.value.code == 2
    output = capsys.readouterr().out
    assert label.replace(":", ":\033[0m") in output
    assert f"  {correction}\n" in output
    assert "  providers\n" not in output


def test_invalid_quality_is_not_called_command(capsys):
    with pytest.raises(SystemExit):
        parse_cli_args(["search", "title", "-q", "900p"])
    output = capsys.readouterr().out
    assert "Invalid quality" in output
    assert "Unknown command" not in output
    assert "Did you mean" not in output
    assert "anyplay search -h" in output


@pytest.mark.parametrize("query", ["animex:", "animex:renegade", "animex:renegade immortal"])
def test_provider_error_layout_preserves_query(query, capsys):
    import shlex
    with pytest.raises(SystemExit) as error:
        parse_cli_args(["search", query])
    assert error.value.code == 2
    output = capsys.readouterr().out
    assert "Unknown provider:\033[0m animex\n\nDid you mean:\n" in output
    suggestion = output.split("Did you mean:\n  ")[1].splitlines()[0]
    assert shlex.split(suggestion) == ["anyplay", "search", query.replace("animex:", "animexin:", 1)]
    assert "usage:" not in output
    assert "Run 'anyplay providers' to list providers." in output


def test_unknown_provider_without_match_has_no_suggestion(capsys):
    with pytest.raises(SystemExit):
        parse_cli_args(["search", "zzzzzz:example"])
    output = capsys.readouterr().out
    assert "Unknown provider:" in output
    assert "Did you mean" not in output
    assert "usage:" not in output


@pytest.mark.parametrize("flag", ["---help", "--helpp", "--hel"])
def test_misspelled_help_flag_suggests_without_running(flag, capsys):
    with pytest.raises(SystemExit) as error:
        parse_cli_args(["search", flag])
    assert error.value.code == 2
    output = capsys.readouterr().out
    assert f"Unknown option:\033[0m {flag}" in output
    assert "  --help\n" in output
    assert "Run 'anyplay search -h'" in output
    assert "usage:" not in output


@pytest.mark.parametrize("argv,help_command", [
    (["--helpp"], "anyplay"),
    (["anilist", "search", "title", "--helpp"], "anyplay anilist search"),
    (["downloads", "--helpp"], "anyplay downloads"),
])
def test_flag_suggestions_use_command_scope(argv, help_command, capsys):
    with pytest.raises(SystemExit) as error:
        parse_cli_args(argv)
    assert error.value.code == 2
    output = capsys.readouterr().out
    assert "  --help\n" in output
    assert f"Run '{help_command} -h'" in output


def test_animethemes_flag_suggestions_do_not_offer_episode_options(capsys):
    with pytest.raises(SystemExit):
        parse_cli_args(["search", "animethemes:idol", "--episod"])
    output = capsys.readouterr().out
    assert "Unknown option" in output
    assert "Did you mean" not in output


def test_downloader_unknown_flags_still_pass_through():
    args, _ = parse_cli_args(["search", "title", "--download", "--retry-sleep", "5"])
    assert args.extra_args == ["--retry-sleep", "5"]


def test_flag_correction_preserves_equals_value(capsys):
    with pytest.raises(SystemExit):
        parse_cli_args(["search", "title", "--qualty=720p"])
    assert "  --quality=720p\n" in capsys.readouterr().out
