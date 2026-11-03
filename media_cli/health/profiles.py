"""Lightweight diagnostic capabilities; never import or modify provider adapters."""

import re

PROFILES = {
    'torrent': {'name': 'Torrents', 'identity': 'metadata', 'combined': True, 'search_only': True, 'default': 'catalogue', 'levels': ('catalogue',)},
    'animethemes': {'name': 'AnimeThemes', 'identity': 'metadata', 'combined': True, 'search_only': True, 'default': 'catalogue', 'levels': ('catalogue',), 'metadata_origin': 'AnimeThemes'},
    'movies': {'name': 'Movies', 'identity': 'native_id', 'combined': True, 'tracker_optional': True, 'default': 'extraction', 'levels': ('extraction',), 'resolve_embeds': True, 'metadata_origin': 'TMDB', 'catalogue_origin': 'metadata', 'catalogue_note': 'Movie slot or TV episodes are metadata-derived; streaming-site availability is independently checked by source resolution.'},
    'animexin': {'name': 'AnimeXin', 'title_candidates': True, 'source_limit': 1, 'identity': 'metadata', 'combined': True, 'tracker_optional': True, 'url_hosts': ('animexin.dev', 'animexin.vip'), 'default': 'http', 'levels': ('extraction', 'http', 'mpv'), 'resolve_embeds': True},
    'animegg': {'name': 'AnimeGG', 'title_candidates': True, 'slug_episode': True, 'identity': 'metadata', 'combined': True, 'default': 'http', 'levels': ('extraction', 'http', 'mpv'), 'resolve_embeds': True},
    'anizone': {'name': 'AniZone', 'identity': 'metadata', 'combined': True, 'default': 'http', 'levels': ('extraction', 'http', 'mpv'), 'catalogue_origin': 'site_or_fallback', 'catalogue_note': 'Usable episode IDs returned; origin not exposed. Episode 1 may be a fallback after a catalogue error; real stream resolution is independent evidence.'},
    'anidb': {'name': 'AniDB.app', 'identity': 'metadata', 'combined': True, 'default': 'http', 'levels': ('extraction', 'http', 'mpv')},
    'animepahe': {'name': 'AnimePahe', 'identity': 'metadata', 'combined': True, 'default': 'extraction', 'levels': ('extraction',), 'compound_episode': True, 'resolve_embeds': True},
    'anikoto': {'name': 'Anikoto', 'identity': 'anilist_id', 'combined': True, 'default': 'http', 'levels': ('extraction', 'http', 'mpv'), 'metadata_origin': 'AniList', 'catalogue_origin': 'site_or_metadata'},
    'senshi': {'name': 'Senshi', 'identity': 'mal_id', 'combined': False, 'default': 'http', 'levels': ('extraction', 'http', 'mpv')},
    'anipm': {'name': 'AniPM', 'identity': 'mal_id', 'combined': True, 'default': 'http', 'levels': ('extraction', 'http', 'mpv')},
    'reanime': {'name': 'Re:ANIME', 'identity': 'anilist_id', 'combined': True, 'default': 'http', 'levels': ('extraction', 'http', 'mpv')},
}

PROFILES['lucifer'] = {**PROFILES['animexin'], 'name': 'LuciferDonghua', 'url_hosts': ('luciferdonghua.in',), 'default': 'extraction', 'levels': ('extraction',)}
PROFILES['animekhor'] = {**PROFILES['animexin'], 'name': 'AnimeKhor', 'url_hosts': ('animekhor.org', 'animekhor.xyz'), 'default': 'extraction', 'levels': ('extraction',)}


def valid_title_id(provider_id, value):
    if not isinstance(value, str):
        return False
    if PROFILES[provider_id].get('url_hosts'):
        from urllib.parse import urlsplit, unquote
        try:
            parsed = urlsplit(value)
            return bool(parsed.scheme == 'https' and parsed.hostname in PROFILES[provider_id]['url_hosts'] and parsed.port in (None, 443) and not parsed.username and not parsed.password and not parsed.query and not parsed.fragment and not any(part in ('.', '..') for part in unquote(parsed.path).split('/')) and not any(ord(c) < 33 or ord(c) == 127 for c in value))
        except ValueError:
            return False
    if provider_id in ('reanime', 'animepahe', 'anizone', 'animegg'):
        return bool(re.fullmatch(r'[a-zA-Z0-9_-]{1,200}', value))
    if provider_id == 'movies':
        return bool(re.fullmatch(r'(?:movie|tv):[1-9][0-9]*', value))
    if provider_id == 'anipm':
        return bool(re.fullmatch(r'(?:mal-)?[1-9][0-9]*', value))
    return bool(re.fullmatch(r'[1-9][0-9]*', value))


def matching_rows(provider_id, sample, rows):
    key = PROFILES[provider_id]['identity']
    field = 'malId' if key == 'mal_id' else 'provider_id' if key == 'native_id' else 'anilistId'
    if key != "metadata" and not sample.get(key):
        return []
    matched = []
    for row in rows:
        if key == "metadata" or not isinstance(row, dict) or str(row.get(field)) != str(sample[key]):
            continue
        if any(sample.get(expected) and row.get(actual) and str(sample[expected]) != str(row[actual])
               for expected, actual in (('mal_id', 'malId'), ('anilist_id', 'anilistId'))):
            continue
        matched.append(row)
    if matched:
        return [{**row, 'match_method': 'id'} for row in matched]
    # Missing IDs may use the normal matcher, with release evidence required.
    # Explicit conflicts are never repaired by a similar title.
    from ..domain.matching import choose_confident_match, tracker_id_contradiction, _extract_item_year
    reference = {'name': sample['title'], 'airedStart': {'year': sample['year']},
                 'malId': sample.get('mal_id'), 'aniListId': sample.get('anilist_id')}
    candidates = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        candidate = {**row, 'aniListId': row.get('anilistId')}
        if tracker_id_contradiction(reference, candidate):
            continue
        # Fallback is for absent identity, not a different known identity.
        if str(row.get(field) or '0') != '0':
            continue
        if not PROFILES[provider_id].get('title_candidates') and not _extract_item_year(row):
            continue
        candidates.append(candidate)
    best = choose_confident_match(reference, candidates)
    return [{**best, 'match_method': 'title_candidate' if PROFILES[provider_id].get('title_candidates') and not _extract_item_year(best) else 'metadata'}] if best else []


def verified_locator(provider_id, sample, rows):
    locators = {(str(row.get('provider_id') or ''), str(row.get('anilistId') or '')) for row in rows}
    if len(locators) != 1:
        return {}
    title_id, anilist_id = next(iter(locators))
    if not valid_title_id(provider_id, title_id):
        return {}
    if provider_id in ('anipm', 'anikoto'):
        if provider_id == 'anipm' and title_id == f"mal-{sample['mal_id']}" and not anilist_id:
            return {'provider_title_id': title_id}
        if not (title_id.isdigit() and anilist_id.isascii() and anilist_id.isdigit() and int(title_id) == int(anilist_id) > 0):
            return {}
    if (provider_id == 'reanime' or PROFILES[provider_id]['identity'] in ('metadata', 'native_id')) and anilist_id in ('', '0'):
        return {'provider_title_id': title_id}
    if not (anilist_id.isascii() and anilist_id.isdigit() and int(anilist_id) > 0):
        return {}
    return {'provider_title_id': title_id, 'anilist_id': int(anilist_id)}


def qualify_evidence(provider):
    """Describe adapter evidence without changing or guessing its origin."""
    profile = PROFILES[provider['id']]
    metadata_origin = profile.get('metadata_origin')
    catalogue_origin = profile.get('catalogue_origin')
    for stage in provider['stages']:
        if stage.get('mal_id') is None:
            stage.pop('mal_id', None)
        if metadata_origin and stage['name'] in ('search', 'title_identity', 'title_details'):
            stage['evidence_origin'] = metadata_origin
            stage['reason'] = f"{metadata_origin} metadata: " + stage.get('reason', '')
        if catalogue_origin and stage['name'] == 'episode_catalogue':
            stage['evidence_origin'] = catalogue_origin
            if stage['status'] == 'passed':
                stage.update(status='inconclusive', reason=profile.get('catalogue_note') or 'Usable episode IDs returned; origin not exposed (site or AniList fallback). Site catalogue not independently verified; real stream resolution is independent evidence.')


def valid_episode_id(provider_id, value, title_id=None):
    if type(value) not in (str, int, float):
        return False
    text = str(value)
    if PROFILES[provider_id].get('url_hosts'):
        return valid_title_id(provider_id, text)
    if PROFILES[provider_id].get('slug_episode'):
        return valid_title_id(provider_id, text) and bool(title_id) and text.startswith(title_id + '-')
    if PROFILES[provider_id].get('compound_episode'):
        parts = text.split('/')
        return len(parts) == 2 and parts[0] == title_id and all(valid_title_id(provider_id, part) for part in parts)
    return text.replace('.', '', 1).isdigit()
