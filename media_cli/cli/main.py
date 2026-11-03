"""Small installed entry point; load the media application only when needed."""

import sys


def run():
    from ..core.startup import begin_startup

    begin_startup()
    try:
        from .args import parse_cli_args

        parsed = parse_cli_args()
        args, _ = parsed
        if args.command == "health":
            from .health import handle_health_command

            return handle_health_command(args.health_action, args.health_report_id, plain=args.plain, delete=args.health_delete, seed=args.health_seed, verify=args.health_verify, translation=args.health_translation or "sub")
        if args.command == "companion":
            from .companion import handle_companion_command

            return handle_companion_command(args.companion_action)
        if args.command == "completion":
            from .completion import handle_completion_command

            try:
                handle_completion_command(args)
            except OSError as error:
                print(f"Could not write shell completion: {error}", file=sys.stderr)
                return 1
            return 0

        from .application import run as run_application

        return run_application(parsed)
    except KeyboardInterrupt:
        return 0 if sys.stdin.isatty() else 130
