"""One bounded provider catalogue operation; output is structured evidence."""

import contextlib
import io
import json
import sys
import time

from .profiles import PROFILES, valid_title_id, valid_episode_id


def _probe_result(entries, result, provider_id, operation):
    from .mirrors import concrete_entries
    if not result.get('mirrors'):
        return
    if operation in ('http', 'mpv', 'resolve_http', 'resolve_mpv'):
        try:
            from .http_media import probe_entries, media_destination_allowed
            result['media_checks'] = probe_entries(concrete_entries(entries)[0],
                **({'allowed': lambda url: media_destination_allowed(provider_id, url)} if provider_id != 'senshi' else {}))
        except Exception as error:
            from .failures import classify_failure
            result['http_failure'] = classify_failure(error)
    if operation in ('mpv', 'resolve_mpv'):
        try:
            from .mpv_media import probe_entries
            result['playback_checks'] = probe_entries(concrete_entries(entries)[0])
        except Exception as error:
            from .failures import classify_failure
            result['playback_failure'] = classify_failure(error)


def _theme_search(query, translation):
    from ..services.animethemes import search
    payload = search(query)
    return [{'_id': str(row.get('id') or ''), 'name': row.get('name') or '',
             'airedStart': {'year': row.get('year')}} for row in payload.get('anime') or [] if isinstance(row, dict)]


def main():
    request = json.loads(sys.stdin.read())
    provider_id = request.get("provider")
    if provider_id not in PROFILES:
        return 2
    translation = request.get("translation", "sub")
    if translation not in ("sub", "dub"):
        return 2
    operation = request.get("operation", "search")
    if operation not in ("search", "details", "episodes", "sources", "extract", "http", "mpv", "resolve", "resolve_http", "resolve_mpv"):
        return 2
    if PROFILES[provider_id]["combined"] and operation not in ("search", "details", "episodes", "resolve", "resolve_http", "resolve_mpv"):
        return 2
    if PROFILES[provider_id].get("search_only") and operation != "search":
        return 2
    if operation.startswith("resolve") and not PROFILES[provider_id]["combined"]:
        return 2
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            from ..providers import PROVIDER_FACTORIES
            from ..provider_manifest import PROVIDER_REGISTRY
            from ..services.requirements import require_provider_operation

            require_provider_operation(provider_id, "title" if operation == "details" else "sources" if operation.startswith("resolve") else operation)
            if provider_id == 'animethemes':
                from types import SimpleNamespace
                provider = SimpleNamespace(search=_theme_search)
            else:
                provider = PROVIDER_FACTORIES[provider_id]()
            provider.metadata = PROVIDER_REGISTRY[provider_id]
            provider.domains = list(provider.metadata.get("domains") or ())
            if provider_id == 'movies':
                from ..core.tmdb import TMDBError
                try:
                    provider.tmdb
                except TMDBError:
                    from ..core.api import ProviderDependencyError
                    raise ProviderDependencyError('Movies health requires a configured TMDB token; metadata was not checked')
            if operation == "search":
                rows = provider.search(request["value"], translation)
                result = {"status": "ok", "results": [
                    {**{key: row[key] for key in ("englishName", "romajiName", "nativeName", "airedStart", "season", "type", "format", "episodeCount", "altNames") if key in row},
                     "malId": row.get("malId"), "name": row.get("name", ""),
                     **({"anilistId": row.get("aniListId") or row.get("anilistId") or row.get("anilist_id"), "provider_id": row.get("_provider_id") or row.get("_id")} if PROFILES[provider_id]["combined"] else {})}
                    for row in rows if isinstance(row, dict)
                ]}
            else:
                supplied = request["value"]
                if PROFILES[provider_id]["combined"] and not isinstance(supplied, dict):
                    return 2
                value = str(supplied.get("mal_id") if operation in ("sources", "extract", "http", "mpv", "resolve", "resolve_http", "resolve_mpv") else supplied.get("mal_id") if PROFILES[provider_id]["combined"] and isinstance(supplied, dict) else supplied)
                optional_tracker = PROFILES[provider_id].get("tracker_optional") and isinstance(supplied, dict) and supplied.get("mal_id") is None
                if not optional_tracker and (not value.isascii() or not value.isdigit() or int(value) <= 0):
                    return 2
                title_id = str(supplied.get("title_id") or "") if PROFILES[provider_id]["combined"] and isinstance(supplied, dict) else value
                if not valid_title_id(provider_id, title_id):
                    return 2
                if operation == "details":
                    from .details import assess_details
                    result = {"status": "ok", "details": [assess_details(provider.get_title(title_id), value,
                              expected_anilist_id=supplied.get("anilist_id") if PROFILES[provider_id]["combined"] else None)]}
                elif operation in ("sources", "extract", "http", "mpv", "resolve", "resolve_http", "resolve_mpv"):
                    episode = str(supplied["episode"])
                    if not valid_episode_id(provider_id, episode, title_id):
                        return 2
                    if operation.startswith("resolve"):
                        from .mirrors import summarize_mirrors
                        before = time.monotonic()
                        payload = provider.episode_sources(title_id, episode, translation)
                        entries = (payload or {}).get("episode", {}).get("sourceUrls") or []
                        descriptors = list(entries)
                        sampled_source = None
                        if PROFILES[provider_id].get('source_limit') == 1 and descriptors:
                            import random
                            entries = [random.Random(supplied.get('sampling_seed', 0)).choice(descriptors)]
                            sampled_source = str(entries[0].get('sourceName') or 'Unnamed source')
                        if PROFILES[provider_id].get('resolve_embeds'):
                            from ..media.resolver import resolve_source
                            entries_to_resolve = list(entries)
                            entries = []
                            for descriptor in entries_to_resolve:
                                entries.extend(resolve_source(descriptor, silent=True) if descriptor.get('type') == 'embed' else [descriptor])
                        result = summarize_mirrors(entries)
                        if sampled_source is not None:
                            result['source'] = sampled_source
                        if PROFILES[provider_id].get('resolve_embeds'):
                            result['source_count'] = len(descriptors)
                            result['source_names'] = [str(d.get('sourceName') or 'Unnamed source') for d in descriptors]
                        result["extraction_elapsed_seconds"] = time.monotonic()-before
                        _probe_result(entries, result, provider_id, operation)
                    elif operation in ("extract", "http", "mpv"):
                        from .mirrors import summarize_mirrors
                        before = time.monotonic()
                        entries = provider.extract_discovered_source(value, episode, supplied["source"], translation)
                        result = summarize_mirrors(entries)
                        result["extraction_elapsed_seconds"] = time.monotonic()-before
                        _probe_result(entries, result, provider_id, operation)
                    else:
                        result = {"status": "ok", "sources": provider.discover_episode_sources(value, episode, translation)}
                else:
                    catalog = provider.episode_catalog(title_id, translation)
                    result = {"status": "ok", "ids": catalog.get("ids") or []}
    except Exception as error:
        from .failures import classify_failure
        result = classify_failure(error)
        result[{"search": "results", "details": "details", "episodes": "ids", "sources": "sources", "extract": "mirrors", "http": "mirrors", "mpv": "mirrors", "resolve": "mirrors", "resolve_http": "mirrors", "resolve_mpv": "mirrors"}[operation]] = []
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    try:
        exit_code = main()
    finally:
        # A provider may import display helpers, but this worker never owns a TUI.
        # Their process-exit restore would corrupt JSON and reset the caller's tty.
        display = sys.modules.get("media_cli.ui.display")
        if display is not None:
            import atexit
            atexit.unregister(display.restore_terminal)
    raise SystemExit(exit_code)
