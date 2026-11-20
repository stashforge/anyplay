"""Small installed entry point; load the media application only when needed."""

import sys


def run():
    from ..core.startup import begin_startup

    begin_startup()
    try:
        from .args import parse_cli_args

        parsed = parse_cli_args()
        args, parser = parsed
        from ..context import FLAGS
        FLAGS.incognito_mode = bool(getattr(args, "incognito", False))
        if FLAGS.incognito_mode and args.command == "service" and args.service_action != "status":
            parser.error("--incognito cannot manage or run the persistent History service")
        if args.command == "theme":
            if args.theme_action == "list":
                from .themes import list_themes
                return list_themes()
            # Select before importing any TUI theme consumers.
            import os
            os.environ['ANYPLAY_THEME'] = args.theme_name
            preview_args = ['search']
            if args.incognito:
                preview_args.append('--incognito')
            if args.debug:
                preview_args.append('--debug')
            parsed = parse_cli_args(preview_args)
            args, parser = parsed
        if args.command == "service":
            from .service import handle_service_command

            return handle_service_command(args.service_action)
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
