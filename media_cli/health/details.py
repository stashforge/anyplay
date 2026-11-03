"""Assess adapter metadata without enrichment or publishing raw field values."""

import re
from urllib.parse import urlsplit

from ..domain.health_report import TITLE_DETAIL_FIELDS as FIELDS


def _text(value):
    return isinstance(value, str) and bool(value.strip()) and value.strip().casefold() not in ('unknown', 'n/a', 'none', '-')


def _id(value):
    return type(value) in (str, int) and str(value).isascii() and str(value).isdigit() and int(value) > 0


def assess_details(payload, expected_mal_id, *, expected_anilist_id=None):
    value = payload if isinstance(payload, dict) else {}
    title = value.get('name')
    placeholder = isinstance(title, str) and bool(re.fullmatch(r'\s*(?:mal|anilist)\s+id\s*:\s*\d+\s*', title, re.IGNORECASE))
    cover = value.get('thumbnail') or ''
    try:
        parsed = urlsplit(cover) if isinstance(cover, str) else None
        cover_ok = bool(parsed and parsed.scheme in ('http', 'https') and parsed.hostname and not parsed.username and not parsed.password)
    except ValueError:
        cover_ok = False
    season = value.get('season') if isinstance(value.get('season'), dict) else {}
    year = value.get('year') or value.get('seasonYear') or value.get('releaseYear') or season.get('year')
    anilist_id = value.get('aniListId') or value.get('anilistId') or value.get('anilist_id') or value.get('_anilist_id')
    present = {
        'title': bool(_text(title) and not placeholder),
        'alternate_title': any(_text(value.get(k)) and value[k] != title for k in ('romajiName', 'englishName', 'nativeName')),
        'cover': cover_ok, 'description': bool(_text(value.get('description'))),
        'format': bool(_text(value.get('format'))), 'status': bool(_text(value.get('status'))),
        'year': bool(_id(year) and 1800 <= int(year) <= 9999),
        'mal_id': bool(_id(value.get('malId'))), 'anilist_id': bool(_id(anilist_id)),
    }
    conflict = present['mal_id'] and _id(expected_mal_id) and int(value['malId']) != int(expected_mal_id)
    anilist_conflict = present['anilist_id'] and _id(expected_anilist_id) and int(anilist_id) != int(expected_anilist_id)
    fallback = bool(value.get('_provider_title_fallback'))
    # A provider-local ID or URL can be echoed alongside a synthesized name.
    substantive = any(present[k] for k in FIELDS if k != 'title')
    usable = present['title'] and substantive and not fallback
    status = 'failed' if conflict or anilist_conflict else 'passed' if usable else 'inconclusive'
    reason = ('Explicit AniList ID conflicts with the verified search result' if anilist_conflict else
              'Explicit MAL ID conflicts with the requested title' if conflict else
              'Adapter returned only a placeholder title; substantive details not verified' if placeholder else
              'Adapter declared a fallback title; substantive details not verified' if fallback else
              'Usable title metadata returned; optional field presence recorded, values not independently verified' if usable else
              'Adapter returned title-only metadata; detail retrieval not verified because the title may be synthesized' if present['title'] else
              'No usable title metadata returned; request failure and missing details cannot be distinguished')
    return {'status': status, 'reason': reason,
            'present_fields': [k for k in FIELDS if present[k]],
            'missing_fields': [k for k in FIELDS if not present[k]]}
