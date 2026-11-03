"""Record shared media probe outcomes without losing earlier extraction evidence."""

from .failures import record_local_requirement

_FIELDS = ('status', 'reason', 'track', 'subtitle_label', 'mirror', 'mirror_index', 'http_status',
           'bytes_sampled', 'host', 'elapsed_seconds', 'decoded_frames', 'decoded_samples', 'channel', 'required')


def record_probe_evidence(report, provider, common, outcome, *, playback=False):
    name, checks_key, failure_key = ('playback', 'playback_checks', 'playback_failure') if playback else ('media_access', 'media_checks', 'http_failure')
    failure = outcome.get(failure_key)
    if failure:
        stage = {'name': name, 'status': 'inconclusive', **common,
                 'reason': failure.get('reason') or 'Media sample not verified'}
        provider['stages'].append(stage)
        return record_local_requirement(report, provider, stage, failure)
    checks = outcome.get(checks_key) or []
    if not checks:
        provider['stages'].append({'name': name, 'status': 'not_verified', **common,
                                   'reason': 'No decoded sample evidence returned' if playback else 'No HTTP sample evidence; extraction may not have produced usable entries'})
    for check in checks:
        evidence = {key: check[key] for key in _FIELDS if key in check}
        provider['stages'].append({**common, **evidence, 'name': name})
    return False


def record_completed_probes(report, provider, common, outcome, *, http=False, playback=False):
    """Keep every returned probe result before stopping later samples."""
    stop = False
    if http:
        stop = record_probe_evidence(report, provider, common, outcome) or stop
    if playback:
        stop = record_probe_evidence(report, provider, common, outcome, playback=True) or stop
    return stop
