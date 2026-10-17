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


def build_command_parser(search_provider=None):
    parser = _configure_help_parser(CLIParser(
        prog=APP_NAME,
        usage=f"{APP_NAME} <command> [options]",
        description="Watch anime and media from multiple providers with optional AniList integration.",
        add_help=False,
        epilog=(
            "Quick start:\n"
            f"  {APP_NAME} search frieren\n"
            f'  {APP_NAME} search animexin:"renegade immortal"\n'
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

    register_all_subcommands(commands, search_provider)
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

    if meaningful[:2] == ["anilist", "search"]:
        debug_enabled = "--debug" in argv
        nested_argv = meaningful[2:]
        if debug_enabled:
            nested_argv.append("--debug")
        parser = build_anilist_search_parser()
        return parser.parse_args(nested_argv), parser
    parser = build_command_parser()
    search_provider = None
    command_index = next((i for i, word in enumerate(argv) if not word.startswith("-")), None)
    if command_index is not None and argv[command_index] in _provider_command_names() | {"animethemes"}:
        import shlex
        provider = argv[command_index]
        rest = argv[command_index + 1:]
        if rest[:1] == ["search"]:
            rest = rest[1:]
        prefix = provider + ":"
        if rest and not rest[0].startswith("-"):
            rest = [prefix + rest[0], *rest[1:]]
        else:
            rest = [prefix, *rest]
        parser._command_suggestion = shlex.join([APP_NAME, *argv[:command_index], "search", *rest])
    if command_index is not None and argv[command_index] == "search":
        # Skip option values so only the first query word can select a provider.
        search_parser = parser._subparsers._group_actions[0].choices["search"]
        index = command_index + 1
        while index < len(argv):
            word = argv[index]
            if word == "--":
                index += 1
                break
            if not word.startswith("-"):
                break
            option = search_parser._option_string_actions.get(word.split("=", 1)[0])
            index += 2 if option and option.nargs != 0 and "=" not in word else 1
        if index < len(argv) and ":" in argv[index]:
            search_provider, _, query_start = argv[index].partition(":")
            valid = _provider_command_names() | {"animethemes"}
            if search_provider not in valid:
                import shlex
                from .suggestions import suggest
                matches = suggest(search_provider, valid)
                print(f"\n\033[31mUnknown provider:\033[0m {search_provider}\n")
                if matches:
                    print("Did you mean:")
                    for match in matches[:1]:
                        corrected = list(argv)
                        corrected[index] = f"{match}:{query_start}"
                        print(f"  {shlex.join([APP_NAME, *corrected])}")
                    print()
                print(f"Run '{APP_NAME} providers' to list providers.\n")
                parser.exit(2)
            argv[index:index + 1] = [query_start] if query_start else []
            parser = build_command_parser(search_provider)
    args, extra = parser.parse_known_args(argv)
    if search_provider and search_provider != "animethemes":
        explicit_provider = getattr(args, "provider", None)
        if explicit_provider and explicit_provider != search_provider:
            parser.error("provider prefix conflicts with --provider")
        args.provider = search_provider

    removed_flags = {"--sidecar-subs", "--embed-subs"}
    if any(arg in removed_flags for arg in extra):
        parser.error("subtitle sidecar/embed mode flags were removed; downloads now embed subtitles into MKV automatically")

    if extra:
        if getattr(args, "download", False):
            args.extra_args = extra
        else:
            import shlex
            error_parser = parser._subparsers._group_actions[0].choices.get(args.command, parser)
            error_parser.error(f"unrecognized arguments: {shlex.join(extra)}")

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
    return args, parser
