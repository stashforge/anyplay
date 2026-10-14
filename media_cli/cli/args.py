"""Command-line parsing and help formatting for Anime Providers CLI."""

import sys

from ..constants import APP_NAME
from .args_common import (
    ANILIST_COMMAND_TARGETS,
    COMPLETION_SHELLS,
    CLIParser,
    CommandHelpFormatter,
    MinimalHelpFormatter,
    __version__,
    _add_anilist_options,
    _add_debug_option,
    _add_plain_option,
    _configure_help_parser,
    _provider_command_names,
    _set_cli_defaults,
)
from .args_subcommands import register_all_subcommands


def build_command_parser():
    parser = _configure_help_parser(CLIParser(
        prog=APP_NAME,
        usage=f"{APP_NAME} <command> [options]",
        description="Watch anime and media from multiple providers with optional AniList integration.",
        add_help=False,
        epilog=(
            "Quick start:\n"
            f"  {APP_NAME} search frieren\n"
            f"  {APP_NAME} search frieren -e 1 --download\n"
            f"  {APP_NAME} continue\n"
            f"  {APP_NAME} downloads\n"
            f"  {APP_NAME} anilist watching\n\n"
            f"Run '{APP_NAME} <command> --help' for command-specific help.\n\n"
            "Examples:\n"
            f"  {APP_NAME} search --help\n"
            f"  {APP_NAME} downloads --help\n"
            f"  {APP_NAME} anilist --help"
        ),
        formatter_class=CommandHelpFormatter,
    ))
    _set_cli_defaults(parser)
    commands = parser.add_subparsers(dest="command", metavar="<command>", title="Main commands")
    global_options = parser.add_argument_group("Global options")
    global_options.add_argument(
        "-h", "--help", action="help", help="Show this help message and exit"
    )
    global_options.add_argument(
        "-v", "--version", action="version", version=f"%(prog)s {__version__}", help="Show program's version number and exit"
    )
    _add_debug_option(global_options)
    _add_plain_option(global_options)

    register_all_subcommands(commands)
    return parser


def build_anilist_search_parser():
    parser = _configure_help_parser(CLIParser(
        prog=f"{APP_NAME} anilist search",
        usage=f"{APP_NAME} anilist search <query> [options]",
        description="Search anime on AniList.",
        epilog=(
            "Examples:\n"
            f"  {APP_NAME} anilist search erased\n"
            f'  {APP_NAME} anilist search "attack on titan"'
        ),
        add_help=False,
        formatter_class=MinimalHelpFormatter,
    ))
    _set_cli_defaults(parser)
    parser._positionals.title = "Arguments"
    parser.add_argument("query", nargs="*", help="Anime title to search")
    _add_anilist_options(parser, search=True)
    parser.set_defaults(command="anilist", anilist="search")
    return parser


def parse_cli_args(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    meaningful = [arg for arg in argv if arg != "--debug"]

    # Support: anyplay <provider> ...
    if meaningful and meaningful[0] in _provider_command_names():
        p_id = meaningful[0]
        if len(meaningful) > 1 and meaningful[1] == "anilist":
            rest = meaningful[2:]
            if rest and rest[0] == "search":
                parser = build_anilist_search_parser()
                parsed_args = parser.parse_args(rest[1:] + ["--provider", p_id] + (["--debug"] if "--debug" in argv else []))
                return parsed_args, parser
            else:
                parser = build_command_parser()
                parsed_args, _ = parser.parse_known_args(["anilist"] + rest + ["--provider", p_id] + (["--debug"] if "--debug" in argv else []))
                target = str(getattr(parsed_args, "target", "menu") or "menu").lower()
                parsed_args.anilist = ANILIST_COMMAND_TARGETS.get(target, target)
                if hasattr(parsed_args, "target"):
                    del parsed_args.target
                return parsed_args, parser

    if meaningful[:2] == ["anilist", "search"]:
        debug_enabled = "--debug" in argv
        nested_argv = meaningful[2:]
        if debug_enabled:
            nested_argv.append("--debug")
        parser = build_anilist_search_parser()
        return parser.parse_args(nested_argv), parser
    parser = build_command_parser()
    args, extra = parser.parse_known_args(argv)

    removed_flags = {"--sidecar-subs", "--embed-subs"}
    if any(arg in removed_flags for arg in extra):
        parser.error("subtitle sidecar/embed mode flags were removed; downloads now embed subtitles into MKV automatically")

    if extra:
        if getattr(args, "download", False):
            args.extra_args = extra
        else:
            parser.error(f"unrecognized arguments: {' '.join(extra)}")

    if args.command is None:
        parser.print_help()
        parser.exit()
    if args.command == "anilist":
        target = str(args.target or "menu").lower()
        if target == "search":
            parser.error("anilist search requires a query")
        args.anilist = ANILIST_COMMAND_TARGETS[target]
        del args.target
    elif args.command == "auth":
        args.login = args.action == "login"
        args.logout = args.action == "logout"
        args.auth_status = args.action == "status"
        args.auth_token = args.action == "token"
        args.auth_token_raw = bool(args.raw)
        if args.raw and args.action != "token":
            parser.error("auth --raw is only valid with auth token")
        del args.action
    elif args.command == "config":
        args.config_action = getattr(args, "action", None)
        args.config_key = getattr(args, "key", None)
        args.config_value = getattr(args, "value", None)
        args.config_editor = getattr(args, "editor", None)
        if hasattr(args, "action"):
            del args.action
    elif args.command == "completion":
        values = list(args.completion_args)
        if values[0] == "install":
            if len(values) != 2 or values[1] not in COMPLETION_SHELLS:
                parser.error("completion install requires bash, zsh, or fish")
            args.completion_install = True
            args.completion_shell = values[1]
        else:
            if len(values) != 1 or values[0] not in COMPLETION_SHELLS:
                parser.error("completion requires bash, zsh, or fish")
            args.completion_shell = values[0]
        del args.completion_args
    elif args.command == "logs":
        args.logs = True
    elif args.command in _provider_command_names():
        args.download = getattr(args, "download", False) or (getattr(args, "provider_action", "") == "download")
        args.command = "search"
        if hasattr(args, "provider_action"):
            del args.provider_action
    return args, parser
