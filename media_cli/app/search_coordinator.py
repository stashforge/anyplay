"""Asynchronous live search workers and query coordination."""

from __future__ import annotations

import os
import sys
import threading

from ..core.anilist import search_anilist
from ..core.api import SearchFailure, search_failure_message
from ..core.reporting import debug_warn
from ..core.storage import load_config, write_exception_log
from ..domain.titles import get_show_display_title
from ..providers import provider_display_name, provider_key
from ..services.catalog import search_anime
from ..ui import display
from ..ui.footer import Footer, render_footer
from ..ui.spinner import spinner_from_config

_provider_search_cache = {}


def _loading_header(status):
    if not status:
        return ""
    try:
        width = os.get_terminal_size().columns
    except OSError:
        width = 80
    return render_footer(Footer(statuses=(status,)), width)


def _make_background_status(owner_id, spinner_style, *, task_id="search", message="Searching…"):
    from ..ui.actions import ActionBindings
    from ..ui.background_loading import BackgroundLoadingStatus
    from ..ui.loading_model import (
        LoadingInteraction,
        LoadingOwner,
        LoadingPlacement,
        LoadingTaskSpec,
    )

    return BackgroundLoadingStatus(
        LoadingTaskSpec(
            task_id=task_id, owner=LoadingOwner(owner_id, 0),
            message=message, interaction=LoadingInteraction.BACKGROUND,
            placement=LoadingPlacement.FOOTER, actions=ActionBindings(()), delay=0,
        ),
        spinner_style,
    )


def make_provider_oneshot_search(
    query: str, ttype: str, provider_id: str | None = None
):
    loading = True
    results = []
    error = ""
    cfg = load_config()
    spinner_style = spinner_from_config(cfg)
    token = cfg.get("anilist_token")

    from ..context import FLAGS as runtime_flags

    use_sync = runtime_flags.sync_force_on or (
        cfg.get("sync") and not runtime_flags.sync_force_off
    )
    if not use_sync:
        token = ""

    provider_id = provider_key(provider_id)
    provider_name = provider_display_name(provider_id)
    cache_key = (query, ttype, provider_id)

    if cache_key in _provider_search_cache:
        loading = False
        results = _provider_search_cache[cache_key]
        cached_opts = [f"{get_show_display_title(s)}" for s in results]

        def get_results():
            return results

        def get_loading():
            return ""

        def get_error():
            return ""

        def live_fn(q=""):
            return cached_opts, "", True

        return live_fn, get_results, get_loading, get_error

    search_status = _make_background_status(f"provider-search:{provider_id}", spinner_style)
    enrichment_status = _make_background_status(
        f"provider-search:{provider_id}", spinner_style,
        task_id="enrichment", message="Enriching metadata…",
    )
    enriching = False

    def _fetch():
        nonlocal loading, enriching, results, error
        try:
            shows = None
            al_shows = None

            def _fetch_aa():
                nonlocal shows, error
                try:
                    shows = search_anime(
                        query,
                        ttype,
                        raise_errors=True,
                        provider_id=provider_id,
                    )
                except Exception as exc:
                    error = str(exc)
                    shows = []

            def _fetch_al():
                nonlocal al_shows
                al_shows = search_anilist(token, query)

            from ..context import FLAGS

            is_plain = getattr(FLAGS, "plain_mode", False) or not sys.stdin.isatty()

            threads = [threading.Thread(target=_fetch_aa)]
            if token and not is_plain:
                threads.append(threading.Thread(target=_fetch_al))

            for t in threads:
                t.start()
            for t in threads:
                t.join()

            if shows:
                from ..core.enrichment import (
                    enrich_provider_results_background,
                    enrich_provider_results_fast,
                )

                shows, unmatched = enrich_provider_results_fast(shows, al_shows)
                prepared = [s for s in shows if s.get("status") != "NOT_YET_RELEASED"]
                import media_cli.app_core as app_core

                app_core.batch_prepare_shows_display_state(prepared, ttype)
                results.extend(prepared)
                _provider_search_cache[cache_key] = list(results)

                if unmatched and not is_plain:
                    enriching = True
                    from ..ui import picker as _picker_mod

                    def _bg_worker():
                        nonlocal enriching
                        try:

                            def _on_bg_updated():
                                _picker_mod._needs_redraw = True

                            enrich_provider_results_background(
                                unmatched, token, _on_bg_updated
                            )
                        finally:
                            enriching = False
                            _picker_mod._needs_redraw = True

                    threading.Thread(target=_bg_worker, daemon=True).start()

        except Exception as e:
            if not error:
                error = search_failure_message(provider_name, e)
            try:
                write_exception_log("bg_crash.log")
            except Exception as log_error:
                debug_warn("Failed to write background crash log", log_error)
        finally:
            loading = False

    threading.Thread(target=_fetch, daemon=True).start()

    def get_results():
        return results

    def get_loading():
        searching = search_status.read(loading)
        enrichment = enrichment_status.read(enriching and not loading)
        return searching or enrichment or ""

    def get_error():
        return error

    last_res_state = None
    cached_opts = []

    def live_fn(q=""):
        nonlocal last_res_state, cached_opts
        current_state = (len(results), enriching)
        if current_state != last_res_state:
            last_res_state = current_state
            cached_opts = [f"{get_show_display_title(s)}" for s in results]
        from ..context import FLAGS

        is_plain = getattr(FLAGS, "plain_mode", False) or not sys.stdin.isatty()
        is_done = (not loading) if is_plain else (not loading and not enriching)
        return cached_opts, _loading_header(get_loading()), is_done

    return live_fn, get_results, get_loading, get_error


def make_allanime_oneshot_search(
    query: str, ttype: str, provider_id: str | None = None
):
    return make_provider_oneshot_search(query, ttype, provider_id)


def make_anilist_oneshot_search(token: str, initial_query: str):
    results = []
    loading = True
    error = ""
    spinner_style = display._spinner_style
    search_status = _make_background_status("anilist-search", spinner_style)

    def worker():
        nonlocal results, loading, error
        try:
            res = search_anilist(token, initial_query, raise_errors=True)
            if res:
                import media_cli.app_core as app_core

                app_core.batch_prepare_shows_display_state(res, "sub")
                results.extend(res)
        except SearchFailure as exc:
            error = str(exc)
        finally:
            loading = False

    t = threading.Thread(target=worker, daemon=True)
    t.start()

    def get_loading():
        return search_status.read(loading) or ""

    last_count = -1
    cached_opts = []

    def live_fn(q=""):
        nonlocal last_count, cached_opts
        if len(results) != last_count:
            last_count = len(results)
            cached_opts = [f"{get_show_display_title(s)}" for s in results]
        return cached_opts, _loading_header(get_loading()), not loading

    return live_fn, lambda: list(results), get_loading, lambda: error


def _cached_search_results(query_str, query_key, shows_key, make_search):
    import media_cli.app_core as app_core

    if query_str == getattr(app_core, query_key, None) and getattr(
        app_core, shows_key, None
    ):
        shows = getattr(app_core, shows_key)
        return None, lambda: shows, lambda: "", lambda: ""
    return make_search()


def _remember_search_results(query_str, shows, query_key, shows_key):
    if shows:
        import media_cli.app_core as app_core

        setattr(app_core, query_key, query_str)
        setattr(app_core, shows_key, shows)
