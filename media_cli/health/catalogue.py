"""Search and episode-catalogue diagnostics; no enrichment or media operations."""

import json
import random
import subprocess
import sys
import time
from datetime import datetime, timezone

from .failures import record_local_requirement
from .scope import call_for_translation
from .limits import DiagnosticBudgetExceeded, check_budget, remaining_timeout
from .profiles import PROFILES, matching_rows, verified_locator, qualify_evidence, valid_episode_id

SEARCH_TIMEOUT = 12
EXTRACTION_TIMEOUT = 60
HTTP_WORKER_TIMEOUT = 125
MPV_WORKER_TIMEOUT = 170
SENSHI_SAMPLES = (
    {"title": "Naruto", "year": 2002, "mal_id": 20},
    {"title": "Cowboy Bebop", "year": 1998, "mal_id": 1},
    {"title": "Death Note", "year": 2006, "mal_id": 1535},
    {"title": "Demon Slayer", "year": 2019, "mal_id": 38000},
    {"title": "Jujutsu Kaisen", "year": 2020, "mal_id": 40748},
    {"title": "Spy x Family", "year": 2022, "mal_id": 50265},
    {"title": "Chainsaw Man", "year": 2022, "mal_id": 44511},
    {"title": "Frieren", "year": 2023, "mal_id": 52991},
    {"title": "Oshi no Ko", "year": 2023, "mal_id": 52034},
    {"title": "Dandadan", "year": 2024, "mal_id": 57334},
)


# Both adapters serve the anime category; independently selectable provider defaults.
ANIPM_SAMPLES = tuple(dict(sample) for sample in SENSHI_SAMPLES)
_REANIME_ANILIST_IDS = {20:20, 1:1, 1535:1535, 38000:101922, 40748:113415, 50265:140960,
                        44511:127230, 52991:154587, 52034:150672, 57334:171018}
REANIME_SAMPLES = tuple({**sample, 'anilist_id': _REANIME_ANILIST_IDS[sample['mal_id']]} for sample in SENSHI_SAMPLES)
ANIKOTO_SAMPLES = tuple(dict(sample) for sample in REANIME_SAMPLES)
ANIMEPAHE_SAMPLES = tuple(dict(sample) for sample in SENSHI_SAMPLES)
ANIDB_SAMPLES = tuple(dict(sample) for sample in SENSHI_SAMPLES)
ANIZONE_SAMPLES = tuple(dict(sample) for sample in SENSHI_SAMPLES)
ANIMEGG_SAMPLES = tuple(dict(sample) for sample in SENSHI_SAMPLES)
DONGHUA_SAMPLES = tuple({'title': title, 'year': year} for title, year in (
    ("Qin's Moon", 2007), ('Nanocore', 2014), ('Journey to the West', 1999),
    ('Soul Land', 2018), ('Battle Through the Heavens Season 5', 2022), ('A Will Eternal', 2020),
    ('Perfect World', 2021), ('Renegade Immortal', 2023), ('Shrouding the Heavens', 2023), ('Tales of Herding Gods', 2024)))
MOVIE_SAMPLES = tuple({'title': title, 'year': year, 'native_id': 'movie:' + str(identity)} for title, year, identity in (
    ('Casablanca',1942,289), ('The Godfather',1972,238), ('The Matrix',1999,603),
    ('Parasite',2019,496243), ('Dune',2021,438631), ('The Batman',2022,414906),
    ('Everything Everywhere All at Once',2022,545611), ('Oppenheimer',2023,872585),
    ('Dune: Part Two',2024,693134), ('Inside Out 2',2024,1022789)))
HEALTH_SAMPLES = {'torrent': tuple({'title': s['title'], 'year': s['year']} for s in MOVIE_SAMPLES), 'animethemes': tuple(dict(s) for s in SENSHI_SAMPLES), 'movies': MOVIE_SAMPLES, 'animekhor': tuple(dict(sample) for sample in DONGHUA_SAMPLES), 'lucifer': tuple(dict(sample) for sample in DONGHUA_SAMPLES), 'animexin': DONGHUA_SAMPLES, 'animegg': ANIMEGG_SAMPLES, 'anizone': ANIZONE_SAMPLES, 'anidb': ANIDB_SAMPLES, 'animepahe': ANIMEPAHE_SAMPLES, 'anikoto': ANIKOTO_SAMPLES, 'senshi': SENSHI_SAMPLES, 'anipm': ANIPM_SAMPLES, 'reanime': REANIME_SAMPLES}


def age_group(year: int, current_year: int) -> str:
    age = current_year - year
    return "unknown" if age < 0 else "recent" if age <= 3 else "middle" if age <= 10 else "older"


def _run_extraction_worker(command, payload, *, timeout=EXTRACTION_TIMEOUT):
    import os
    import signal

    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, start_new_session=os.name == "posix")
    try:
        stdout, stderr = process.communicate(payload, timeout=timeout)
    except (subprocess.TimeoutExpired, KeyboardInterrupt):
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        except ProcessLookupError:
            pass
        process.communicate()
        raise
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def _query_isolated(provider_id: str, operation: str, value, *, translation="sub") -> dict:
    field = {"search": "results", "details": "details", "episodes": "ids", "sources": "sources", "extract": "mirrors", "http": "mirrors", "mpv": "mirrors", "resolve": "mirrors", "resolve_http": "mirrors", "resolve_mpv": "mirrors"}[operation]
    command = [sys.executable, "-m", "media_cli.health.search_worker"]
    payload = json.dumps({"provider": provider_id, "operation": operation, "value": value, "translation": translation})
    try:
        timeout = (MPV_WORKER_TIMEOUT if operation in ("mpv", "resolve_mpv") else
                   HTTP_WORKER_TIMEOUT if operation in ("http", "resolve_http") else
                   EXTRACTION_TIMEOUT if operation in ("extract", "resolve") else SEARCH_TIMEOUT)
        result = _run_extraction_worker(command, payload, timeout=remaining_timeout(timeout))
        if result.returncode:
            return {"status": "error", field: [], "reason": "Catalogue worker exited without a usable result"}
        payload = json.loads(result.stdout)
        if not isinstance(payload, dict) or not isinstance(payload.get(field), list):
            raise ValueError("Invalid search evidence")
        return payload
    except subprocess.TimeoutExpired:
        check_budget()
        return {"status": "timeout", field: [], "reason": "Catalogue request time budget exceeded; not proof the provider is broken"}
    except OSError as error:
        return {"status": "missing_dependency", "issue_scope": "local", "requirement_kind": "runtime", field: [],
                "reason": f"Diagnostic worker could not start ({type(error).__name__}); provider not checked"}
    except ValueError:
        return {"status": "error", field: [], "reason": "Catalogue worker returned invalid data"}


def search_isolated(provider_id: str, title: str, *, translation="sub") -> dict:
    return _query_isolated(provider_id, "search", title, translation=translation)


def details_isolated(provider_id: str, title_reference: int | dict, *, translation="sub") -> dict:
    return _query_isolated(provider_id, "details", title_reference, translation=translation)


def episodes_isolated(provider_id: str, title_reference: int | dict, *, translation="sub") -> dict:
    return _query_isolated(provider_id, "episodes", title_reference, translation=translation)


def sources_isolated(provider_id: str, mal_id: int, episode: str, *, translation="sub") -> dict:
    return _query_isolated(provider_id, "sources", {"mal_id": mal_id, "episode": episode}, translation=translation)


def extract_isolated(provider_id: str, mal_id: int, episode: str, source: str, *, translation="sub") -> dict:
    return _query_isolated(provider_id, "extract", {"mal_id": mal_id, "episode": episode, "source": source}, translation=translation)


def http_isolated(provider_id: str, mal_id: int, episode: str, source: str, *, translation="sub") -> dict:
    return _query_isolated(provider_id, "http", {"mal_id": mal_id, "episode": episode, "source": source}, translation=translation)


def mpv_isolated(provider_id: str, mal_id: int, episode: str, source: str, *, translation="sub") -> dict:
    return _query_isolated(provider_id, "mpv", {"mal_id": mal_id, "episode": episode, "source": source}, translation=translation)


def resolve_isolated(provider_id: str, title_reference: dict, episode: str, *, translation="sub") -> dict:
    return _query_isolated(provider_id, "resolve", {**title_reference, "episode": episode}, translation=translation)


def resolve_http_isolated(provider_id: str, title_reference: dict, episode: str, *, translation="sub") -> dict:
    return _query_isolated(provider_id, "resolve_http", {**title_reference, "episode": episode}, translation=translation)


def resolve_mpv_isolated(provider_id: str, title_reference: dict, episode: str, *, translation="sub") -> dict:
    return _query_isolated(provider_id, "resolve_mpv", {**title_reference, "episode": episode}, translation=translation)


def _select_episode_samples(samples, seed: int, year: int, per_group: int = 2):
    rng = random.Random(seed)
    selected = []
    for group in ("older", "middle", "recent"):
        pool = list({(s.get("mal_id") or (s["title"], s["year"])): s for s in samples if age_group(s["year"], year) == group}.values())
        selected.extend(rng.sample(pool, min(per_group, len(pool))))
    return selected


def _sample_reference(provider_id, sample):
    if not PROFILES[provider_id]["combined"]:
        return sample["mal_id"]
    if not sample.get("provider_title_id"):
        return None
    return {"title_id": sample["provider_title_id"], "mal_id": sample.get("mal_id"),
            "anilist_id": sample.get("anilist_id")}


def check_catalogue(provider_id: str, samples, *, query=search_isolated, progress=None,
                    episode_query=None, episode_progress=None, details_query=None, details_progress=None, source_query=None, source_progress=None,
                    extraction_query=None, extraction_progress=None, resolution_query=None, resolution_progress=None, http_checks=False, playback_checks=False, seed=None, translation="sub") -> dict:
    if provider_id not in PROFILES:
        raise ValueError("Health diagnostics are not wired for this provider")
    if PROFILES[provider_id]["combined"] and any(value is not None for value in (source_query, extraction_query)):
        raise ValueError("This adapter uses combined source resolution, not separate discovery/extraction")
    if translation not in ("sub", "dub"):
        raise ValueError("A diagnostic run needs one translation: sub or dub")
    started = datetime.now(timezone.utc)
    report = {"started_at": started.isoformat(), "status": "complete", "verification": "catalogue", "providers": []}
    if source_query is not None:
        report["verification"] = "discovery"
    if extraction_query is not None or resolution_query is not None:
        report["verification"] = "extraction"
    if http_checks:
        report["verification"] = "http"
    if playback_checks:
        report["verification"] = "mpv"
    provider = {"id": provider_id, "name": PROFILES[provider_id]["name"], "status": "inconclusive", "stages": []}
    report["providers"].append(provider)
    stages = provider["stages"]
    if tuple(samples) == tuple(HEALTH_SAMPLES.get(provider_id, ())):
        counts = {group: sum(age_group(sample['year'], started.year) == group for sample in samples)
                  for group in ('older', 'middle', 'recent')}
        missing = [f"{group}: {count}/2" for group, count in counts.items() if count < 2]
        if missing:
            stages.append({'name': 'sample_set', 'status': 'inconclusive',
                           'reason': 'Bundled diagnostic samples need refreshing (' + ', '.join(missing) +
                                     '); reduced age coverage is not a provider failure'})
    matched = 0
    matched_samples = []
    successful_catalogues = []
    source_stages = []
    if episode_query is not None or details_query is not None or seed is not None:
        seed = random.SystemRandom().randrange(2**63) if seed is None else seed
        if type(seed) is not int or not 0 <= seed < 2**63:
            raise ValueError("Seed must be a nonnegative 63-bit integer")
        report["seed"] = seed
    try:
        for index, sample in enumerate(samples, 1):
            if progress:
                progress(index, len(samples), sample["title"])
            before = time.monotonic()
            outcome = call_for_translation(query, provider_id, sample["title"], translation=translation)
            elapsed = time.monotonic() - before
            common = {"title": sample["title"], "age_group": age_group(sample["year"], started.year), "translation": translation}
            rows = outcome.get("results") or []
            ok = outcome.get("status") == "ok"
            stages.append({"name": "search", "status": "passed" if ok and rows else "inconclusive",
                           "elapsed_seconds": elapsed, **common,
                           "reason": f"Returned {len(rows)} titles" if rows else outcome.get("reason") or
                           "No titles returned; catalogue absence and hidden request failure cannot be distinguished"})
            if record_local_requirement(report, provider, stages[-1], outcome):
                break
            matches = matching_rows(provider_id, sample, rows) if ok else []
            exact = bool(matches) and matches[0].get("match_method") != "title_candidate"
            matched += exact
            if matches:
                matched_sample = dict(sample)
                if PROFILES[provider_id]['combined']:
                    matched_sample.update(verified_locator(provider_id, sample, matches))
                matched_samples.append(matched_sample)
            identity = PROFILES[provider_id]['identity']
            label = 'MAL' if identity == 'mal_id' else 'provider' if identity == 'native_id' else 'AniList'
            stages.append({"name": "title_identity", "status": "passed" if exact else "inconclusive", **common,
                           "match_method": matches[0].get("match_method", "id") if matches else "unverified",
                           "reason": "Unique title candidate only; tracker identity and release metadata unverified" if matches and not exact else "Expected title/release metadata not verified" if identity == "metadata" and not exact else "Confident title and release metadata match; provider tracker ID missing" if exact and matches[0].get("match_method") == "metadata" else f"Expected {label} ID {sample.get(identity)} " + ("found" if exact else "not verified")})
        if details_query is not None and report["status"] == "complete":
            selected_details = _select_episode_samples(matched_samples, seed, started.year)
            detail_stages = []
            for sample in selected_details:
                stage = {"name": "title_details", "status": "not_verified", "title": sample["title"],
                         "age_group": age_group(sample["year"], started.year), "mal_id": sample.get("mal_id"),
                         "release_year": sample["year"], "translation": translation, "reason": "Selected; not attempted"}
                for key in ("provider_title_id", "anilist_id"):
                    if key in sample:
                        stage[key] = sample[key]
                stages.append(stage)
                detail_stages.append(stage)
            for index, stage in enumerate(detail_stages, 1):
                if details_progress:
                    details_progress(index, len(detail_stages), stage["title"])
                before = time.monotonic()
                lookup = _sample_reference(provider_id, stage)
                if lookup is None:
                    stage["reason"] = "No unambiguous provider title ID returned; MAL ID is not assumed to be an AniList ID"
                    continue
                outcome = call_for_translation(details_query, provider_id, lookup, translation=translation)
                stage["elapsed_seconds"] = time.monotonic()-before
                if record_local_requirement(report, provider, stage, outcome):
                    break
                rows = outcome.get("details")
                if outcome.get("status") == "ok" and isinstance(rows, list) and len(rows) == 1 and isinstance(rows[0], dict):
                    evidence = rows[0]
                    stage.update({key: evidence[key] for key in ("status", "reason", "present_fields", "missing_fields") if key in evidence})
                else:
                    stage.update(status="inconclusive", reason=outcome.get("reason") or "No usable title-details response; not proof of a broken provider")
        if episode_query is not None and report["status"] == "complete":
            selected = _select_episode_samples(matched_samples, seed, started.year)
            selection_stages = []
            for sample in selected:
                stage = {"name": "episode_catalogue", "status": "not_verified", "title": sample["title"],
                         "age_group": age_group(sample["year"], started.year), "mal_id": sample.get("mal_id"),
                         "release_year": sample["year"], "translation": translation, "reason": "Selected; not attempted"}
                for key in ("provider_title_id", "anilist_id"):
                    if key in sample:
                        stage[key] = sample[key]
                stages.append(stage)
                selection_stages.append(stage)
            if len(selected) < 6:
                stages.append({"name": "episode_coverage", "status": "inconclusive",
                               "reason": f"Reduced coverage: selected {len(selected)}/6 episode catalogues from verified matches"})
            for index, (sample, stage) in enumerate(zip(selected, selection_stages), 1):
                if episode_progress:
                    episode_progress(index, len(selected), sample["title"])
                before = time.monotonic()
                lookup = _sample_reference(provider_id, sample)
                if lookup is None:
                    stage["reason"] = "No unambiguous provider title ID returned; episode lookup not attempted"
                    continue
                outcome = call_for_translation(episode_query, provider_id, lookup, translation=translation)
                ids = outcome.get("ids") or []
                valid = isinstance(ids, list) and bool(ids) and all(
                    valid_episode_id(provider_id, value, sample.get("provider_title_id")) for value in ids)
                passed = outcome.get("status") == "ok" and valid
                stage.update(status="passed" if passed else "inconclusive", elapsed_seconds=time.monotonic()-before,
                             episode_count=len(set(str(value) for value in ids)) if valid else 0,
                             reason="Usable episode IDs returned" if passed else outcome.get("reason") or
                             "Empty or invalid catalogue; absence and hidden request failure cannot be distinguished")
                if passed:
                    successful_catalogues.append({**sample, "episode_ids": list(dict.fromkeys(str(value) for value in ids))})
                if record_local_requirement(report, provider, stage, outcome):
                    break
        if resolution_query is not None and report["status"] == "complete":
            from .resolution import check_resolution
            selected = _select_episode_samples(successful_catalogues, seed, started.year, per_group=1)
            check_resolution(provider_id, selected, seed=seed, report=report, provider=provider,
                             query=resolution_query, progress=resolution_progress, http_checks=http_checks, playback_checks=playback_checks, translation=translation)
        if source_query is not None and report["status"] == "complete":
            selected_sources = _select_episode_samples(successful_catalogues, seed, started.year, per_group=1)
            source_stages = []
            rng = random.Random(seed)
            for sample in selected_sources:
                stage = {"name": "source_discovery", "status": "not_verified", "title": sample["title"],
                         "age_group": age_group(sample["year"], started.year), "mal_id": sample.get("mal_id"),
                         "release_year": sample["year"], "translation": translation,
                         "episode": rng.choice(sample["episode_ids"]), "reason": "Selected; not attempted"}
                stages.append(stage)
                source_stages.append(stage)
            if len(selected_sources) < 3:
                stages.append({"name": "source_coverage", "status": "inconclusive",
                               "reason": f"Reduced coverage: selected {len(selected_sources)}/3 source checks from passed catalogues"})
            for index, stage in enumerate(source_stages, 1):
                if source_progress:
                    source_progress(index, len(source_stages), stage["title"])
                before = time.monotonic()
                outcome = call_for_translation(source_query, provider_id, stage["mal_id"], stage["episode"], translation=translation)
                names = outcome.get("sources") or []
                valid = isinstance(names, list) and bool(names) and all(isinstance(name, str) and name.strip() for name in names)
                passed = outcome.get("status") == "ok" and valid
                stage.update(status="passed" if passed else "inconclusive", elapsed_seconds=time.monotonic()-before,
                             source_count=len(names) if valid else 0, source_names=names if valid else [],
                             reason="Source descriptors found; mirrors not extracted" if passed else outcome.get("reason") or
                             "No usable descriptors returned; source absence and request failure cannot be distinguished")
                if record_local_requirement(report, provider, stage, outcome):
                    break
        if extraction_query is not None and report["status"] == "complete":
            extraction_stages = []
            for discovered in source_stages:
                if discovered["status"] != "passed":
                    continue
                sources = list(dict.fromkeys(discovered["source_names"]))
                chosen = set(random.Random(seed).sample(sources, min(3, len(sources))))
                if len(sources) > 3:
                    stages.append({"name": "source_coverage", "status": "inconclusive",
                                   "title": discovered["title"], "translation": translation,
                                   "reason": f"Source budget: sampled 3/{len(sources)} named sources; other sources unverified"})
                for source in sources:
                    stage = {key: discovered[key] for key in ("title", "age_group", "mal_id", "release_year", "translation", "episode")}
                    stage.update(name="mirror_extraction", status="not_verified", source=source, reason="Selected; not attempted")
                    stages.append(stage)
                    if source in chosen:
                        extraction_stages.append(stage)
                    else:
                        stage['reason'] = 'Not attempted: per-episode source budget'
            for index, stage in enumerate(extraction_stages, 1):
                if extraction_progress:
                    extraction_progress(index, len(extraction_stages), stage["title"] + " / " + stage["source"])
                before = time.monotonic()
                outcome = call_for_translation(extraction_query, provider_id, stage["mal_id"], stage["episode"], stage["source"], translation=translation)
                mirrors = outcome.get("mirrors") or []
                valid = isinstance(mirrors, list) and bool(mirrors) and all(isinstance(m, dict) for m in mirrors)
                passed = outcome.get("status") == "ok" and valid
                unresolved = outcome.get("unresolved_count", 0)
                stage.update(status="passed" if passed else "inconclusive", elapsed_seconds=time.monotonic()-before,
                             mirror_count=len(mirrors) if valid else 0, mirrors=mirrors if valid else [],
                             unresolved_count=unresolved if type(unresolved) is int and unresolved >= 0 else 0,
                             reason=("Concrete stream entries extracted; HTTP access reported separately" if http_checks else
                                     "Concrete stream entries extracted; media access not verified") if passed else outcome.get("reason") or
                             "No concrete streams extracted; unresolved embeds are not counted as mirrors")
                if type(outcome.get("extraction_elapsed_seconds")) in (int, float):
                    stage["elapsed_seconds"] = outcome["extraction_elapsed_seconds"]
                if record_local_requirement(report, provider, stage, outcome):
                    break
                from .evidence import record_completed_probes
                common = {key: stage[key] for key in ("title", "age_group", "mal_id", "release_year", "translation", "episode", "source")}
                if record_completed_probes(report, provider, common, outcome,
                                           http=http_checks, playback=playback_checks):
                    break
    except DiagnosticBudgetExceeded:
        report["status"] = "incomplete"
        stages.append({"name": "run_budget", "status": "not_verified",
                       "reason": "Diagnostic run budget reached; remaining checks not attempted, not proof of a broken provider"})
    except KeyboardInterrupt:
        report["status"] = "cancelled"
    unchecked = []
    if not any(stage["name"] == "title_details" for stage in stages):
        unchecked.append("title_details")
    if not any(stage["name"] == "playback" for stage in stages):
        unchecked.append("playback")
    if not any(stage["name"] == "media_access" for stage in stages):
        unchecked.insert(1, "media_access")
    if not any(stage["name"] == "mirror_extraction" for stage in stages):
        unchecked.insert(1, "mirror_extraction")
    if not any(stage["name"] == "source_discovery" for stage in stages):
        unchecked.insert(1, "source_discovery")
    if not any(stage["name"] == "episode_catalogue" for stage in stages):
        unchecked.insert(1, "episode_catalogue")
    if resolution_query is not None:
        for name in ("source_discovery", "mirror_extraction"):
            if name in unchecked:
                unchecked.remove(name)
                stages.append({"name": name, "status": "not_applicable",
                               "reason": "Discovery and extraction are combined; see source_resolution. Separate attempted/failed source counts are not exposed."})
    for name in unchecked:
        stages.append({"name": name, "status": "not_verified", "reason": "Not checked in this diagnostic run"})
    stages.append({"name": "catalogue_summary", "status": "inconclusive",
                   "reason": f"Matched {matched}/{len(samples)} requested titles; actual per-age evidence is above. Whole-provider playback health not established by these samples."})
    from .verdicts import apply_verdicts
    qualify_evidence(provider)
    apply_verdicts(report)
    return report
