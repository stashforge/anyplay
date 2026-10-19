"""Installed command entry point."""

import os
import sys

from .. import app_core as app
from ..constants import APP_NAME
from ..context import FLAGS as runtime_flags
from ..ui.terminal_runtime import current_runtime, terminal_application


def _cleanup():
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
def run():
    exit_code = 0
    try:
        if "--incognito" not in sys.argv:
            try:
                from ..state import paths
                from ..state.config import load_config_file
                load_config_file(paths.CONFIG_PATH)
            except Exception:
                pass
        app.app_main()
    except KeyboardInterrupt:
        app.restore_terminal()
        exit_code = 0 if sys.stdin.isatty() else 130
    except app.ProviderDependencyError as exc:
        app.fatal_terminal_exit(str(exc))
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

    for stream in (sys.stdout, sys.stderr, sys.__stdout__, sys.__stderr__):
        try:
            if stream and not stream.closed:
                stream.flush()
        except Exception:
            pass

    if not os.environ.get("ALLMANGA_NO_OS_EXIT"):
        os._exit(exit_code)
    return exit_code
