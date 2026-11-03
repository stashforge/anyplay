"""Evidence-based, sample-scoped provider health verdicts; no config effects."""


def _scope(stage):
    return tuple(str(stage.get(key) or '') for key in ('title', 'episode', 'translation', 'source'))


def _classify(report, provider):
    level = report.get('verification')
    stages = provider.get('stages', [])
    if level not in ('http', 'mpv'):
        return 'inconclusive', 'Catalogue/extraction evidence does not verify media access or playback.'
    if report.get('status') != 'complete' or provider.get('local_issues'):
        return 'inconclusive', 'The run was interrupted, incomplete or blocked by local requirements.'
    identities = {s.get('title') for s in stages if s['name'] == 'title_identity' and s['status'] == 'passed' and s.get('title')}
    core_names = {'search', 'title_identity', 'title_details', 'episode_catalogue'}
    combined = any(s['name'] == 'source_resolution' for s in stages)
    resolution_names = {'source_resolution'} if combined else {'source_discovery', 'mirror_extraction'}
    core_names |= resolution_names
    if len(identities) < 3 or not core_names <= {s['name'] for s in stages}:
        return 'inconclusive', 'Required stages or the minimum three verified title samples are missing.'
    required = [s for s in stages if s['name'] in core_names or s['name'] == 'episode_coverage']
    media = [s for s in stages if s['name'] == 'media_access' and s.get('required', True)]
    decoded = [s for s in stages if s['name'] == 'playback' and s.get('required', True)]
    resolved = [s for s in stages if s['name'] in ('source_resolution', 'mirror_extraction')]
    for resolution in resolved:
        if resolution["status"] != "passed":
            continue
        rows = [s for s in media if _scope(s) == _scope(resolution)]
        mirrors = resolution.get('mirrors') or []
        expected = resolution.get('mirror_count', len(mirrors))
        if not expected or len({s.get('mirror_index') for s in rows if s.get('track') == 'video'}) < expected:
            return 'inconclusive', 'Required per-mirror video HTTP evidence is missing.'
        if sum(s.get('track') == 'audio' for s in rows) < sum(bool(m.get('separate_audio')) for m in mirrors):
            return 'inconclusive', 'A supplied separate audio track was not checked.'
        if sum(s.get('track') == 'subtitle' for s in rows) < sum(m.get('subtitle_count', 0) for m in mirrors):
            return 'inconclusive', 'A supplied subtitle track was not checked.'
        if level == 'mpv':
            tracks = {s.get('track') for s in decoded if _scope(s) == _scope(resolution)}
            if not {'video', 'audio'} <= tracks:
                return 'inconclusive', 'Required video/audio decoding evidence is missing for a sampled source.'
    required += media + (decoded if level == 'mpv' else [])
    if not media or any(s['status'] not in ('passed', 'failed') for s in required):
        return 'inconclusive', 'Required sampled evidence is uncertain, pending or unsupported; no provider-wide failure inferred.'
    deepest = decoded if level == 'mpv' else [s for s in media if s.get('track') == 'video']
    if any(s['status'] == 'failed' for s in required):
        if any(s['status'] == 'passed' for s in deepest):
            return 'partly_working', 'Some required sampled checks failed while others at this verification level passed; see the per-stage evidence.'
        return 'inconclusive', 'No working media sample was verified at this level; provider-wide failure is not established.'
    scope = 'HTTP' if level == 'http' else 'decode'
    return 'working', f'All required {scope} checks passed for the recorded samples only; whole-library coverage and visual/audible output are not verified.'


def apply_verdicts(report):
    """Classify fresh evidence only; old saved reports are never recomputed."""
    for provider in report.get('providers', []):
        status, reason = _classify(report, provider)
        provider['status'] = status
        summary = next((s for s in provider['stages'] if s['name'] == 'catalogue_summary'), None)
        if summary is not None:
            summary.update(status='passed' if status == 'working' else 'inconclusive', reason=reason)
