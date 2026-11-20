"""Explainable release ranking. Index trust and title checks are not file scans."""
import math
import re
from .formatting import quality_from_name


def tags(name):
    result = [quality_from_name(name)]
    for pattern in (r'\b(?:REMUX|WEB-DL|WEBRip|BluRay|BDRip|HDTV|DVD|CAM|HDCAM|HDTS)\b',
                    r'\b(?:HEVC|AVC|AV1|x264|x265|H[.]?26[45])\b'):
        match = re.search(pattern, name, re.I)
        if match:
            result.append(match.group())
    return tuple(filter(None, result))


def assess(name, *, seeders=None, leechers=None, trusted=None, query=''):
    release_tags = tags(name)
    folded = {tag.casefold() for tag in release_tags}
    seeds = max(0, seeders or 0)
    score = 40 * math.log10(1 + seeds)
    if seeders is not None and leechers is not None:
        score += 15 * seeds / (seeds + max(0, leechers) + 1)
    score += next((boost for tag, boost in (('2160p',18),('1080p',15),('720p',8)) if tag in folded), 0)
    score += next((boost for tag, boost in (('remux',12),('web-dl',10),('bluray',10),('bdrip',10),('webrip',7),('hdtv',3),('dvd',2)) if tag in folded), 0)
    if folded & {'hevc','x265','h265','h.265','av1'}:
        score += 4
    elif folded & {'avc','x264','h264','h.264'}:
        score += 2
    if trusted is True:
        score += 12
    warnings = []
    if seeders == 0:
        warnings.append('zero reported seeds')
        score -= 1000
    elif seeders is not None and seeders <= 2:
        score -= 20
    if folded & {'cam','hdcam','hdts'}:
        warnings.append('camera recording')
        score -= 60
    if re.search(r'\b(?:soundtrack|ost|original score)\b', name, re.I):
        warnings.append('soundtrack')
    if re.search(r'\b(?:making of|behind the scenes|true story|real story)\b', name, re.I):
        warnings.append('related extras')
    terms = set(re.findall(r'\w+', query.casefold()))
    words = set(re.findall(r'\w+', name.casefold()))
    relevance = len(terms & words) / len(terms) if terms else 1
    return {'score': round(score, 1), 'tags': release_tags, 'warnings': tuple(warnings),
            'relevance': relevance, 'reason': 'ranked by title match, swarm, quality and index trust'}


def sort_key(result, query):
    assessment = assess(result.name, seeders=result.seeders, leechers=result.leechers,
                        trusted=result.trusted, query=query)
    available = 1 if result.seeders and result.seeders > 0 else 0 if result.seeders is None else -1
    return (-assessment['relevance'], -available, -assessment['score'], result.name.casefold(), result.infohash)


def decimal_size(size):
    value = max(0, size or 0)
    for unit in ('B','KB','MB','GB','TB','PB'):
        if value < 1000 or unit == 'PB':
            return f'{value:.1f}'.rstrip('0').rstrip('.') + ' ' + unit
        value /= 1000
