"""Media application lifecycle, loaded after lightweight command dispatch."""

import os
import sys

from .. import app_core as app
from ..constants import APP_NAME
from ..context import FLAGS as runtime_flags
from ..core.startup import startup_checkpoint
from ..ui.terminal_runtime import current_runtime, terminal_application

startup_checkpoint("imports")


def _cleanup():
    try:
        from ..app.details_catalog import catalog_jobs
        catalog_jobs.shutdown()
    except Exception:
        pass
    try:
        from ..services.browser_access.operation import shutdown_event
        shutdown_event.set()
        if "media_cli.services.browser_access.integration" in sys.modules:
            from ..services.browser_access.integration import close_browser_sessions
            close_browser_sessions(shutdown=True)
    except Exception:
        pass
    try:
        app.flush_anilist_writes()
    except Exception:
        pass
    try:
        from ..services.catalog import _catalog_executor
        _catalog_executor.shutdown(wait=False, cancel_futures=True)
    except Exception:
        pass
    try:
        from ..media.local_proxy import cleanup_active_local_proxy
        cleanup_active_local_proxy()
    except Exception:
        pass
    try:
        from ..core.storage import cleanup_incognito_cache
        cleanup_incognito_cache()
    except Exception:
        pass
    try:
        app._ipc_player.quit()
    except Exception:
        pass
    try:
        app.kill_active_subprocesses()
    except Exception:
        pass
    try:
        from ..media.download_worker import stop_global_worker
        stop_global_worker()
    except Exception:
        pass


@terminal_application
def run(parsed=None):
    exit_code = 0
    dependency_error = None
    try:
        if "--incognito" not in sys.argv:
            try:
                from ..state import paths
                from ..state.config import load_config_file
                load_config_file(paths.CONFIG_PATH)
            except Exception:
                pass
        if parsed is None:
            app.app_main()
        else:
            app.app_main(parsed)
    except KeyboardInterrupt:
        app.restore_terminal()
        exit_code = 0 if sys.stdin.isatty() else 130
    except app.ProviderDependencyError as exc:
        dependency_error = str(exc)
        exit_code = 1
    except Exception as exc:
        app.restore_terminal()
        try:
            log_path = app.write_exception_log("crash.log")
        except Exception as log_error:
            log_path = ""
            if runtime_flags.debug_mode:
                print(
                    f"\n{app.RED}Could not save crash traceback: "
                    f"{log_error}{app.RESET}"
                )
        if runtime_flags.debug_mode:
            print(f"\n{app.RED}An unexpected error occurred!{app.RESET}")
            if log_path:
                print(
                    f"{app.YELLOW}Traceback saved to "
                    f"{log_path}{app.RESET}"
                )
            exit_code = 1
        else:
            print(
                f"\n{app.RED}An unexpected error occurred: "
                f"{exc}{app.RESET}"
            )
            print(
                f"{app.YELLOW}Tip: run '{APP_NAME} logs --tail 80' for recent diagnostics, "
                f"or rerun with --debug for more detail.{app.RESET}"
            )
            exit_code = 1
    finally:
        try:
            try:
                runtime = current_runtime()
                if runtime is not None:
                    runtime.prepare_shutdown()
            finally:
                _cleanup()
        finally:
            try:
                app.restore_terminal()
            except Exception:
                pass

    if dependency_error:
        print(f"\n{dependency_error}\n", file=sys.stderr)

    from ..core.reporting import take_deferred_notices
    for notice in take_deferred_notices():
        print(notice, file=sys.stderr)

    for stream in (sys.stdout, sys.stderr, sys.__stdout__, sys.__stderr__):
        try:
            if stream and not stream.closed:
                stream.flush()
        except Exception:
            pass

    if not os.environ.get("ALLMANGA_NO_OS_EXIT"):
        os._exit(exit_code)
    return exit_code
