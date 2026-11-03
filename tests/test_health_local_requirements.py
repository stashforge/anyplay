"""Local prerequisites must never become evidence of a broken provider."""

import importlib.util
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from media_cli.core.api import ProviderDependencyError
from media_cli.health import catalogue
from media_cli.services.requirements import MissingExecutable, MissingPythonDependency
from media_cli.services.android_webview.client import CompanionSetupRequired, CompanionUnavailable


def classify(error):
    assert importlib.util.find_spec('media_cli.health.failures'), 'Shared health failure classifier missing'
    from media_cli.health.failures import classify_failure
    return classify_failure(error)


@pytest.mark.parametrize('error,kind', [
    (MissingPythonDependency(('m3u8',), 'Extraction'), 'python'),
    (MissingExecutable('node', 'Extraction'), 'executable'),
    (ProviderDependencyError('Node cannot start'), 'runtime'),
    (CompanionSetupRequired('Companion pairing required'), 'companion'),
    (CompanionUnavailable('Companion is unavailable'), 'companion'),
])
def test_known_local_failures_have_explicit_scope(error, kind):
    outcome = classify(error)
    assert outcome['status']=='missing_dependency'
    assert outcome['issue_scope']=='local'
    assert outcome['requirement_kind']==kind
    assert outcome['reason']


def test_unknown_exceptions_are_not_guessed_to_be_provider_or_companion_failures():
    outcome=classify(RuntimeError('remote body must not be dumped'))
    assert outcome['status']=='error' and outcome['issue_scope']=='unknown'
    assert outcome['reason']=='RuntimeError'


def run(outcome):
    samples=catalogue.SENSHI_SAMPLES[:3]
    return catalogue.check_catalogue('senshi',samples,query=lambda *args: outcome)


def test_dependency_report_keeps_provider_inconclusive_and_blocked_stage_unverified(tmp_path):
    from media_cli.state.health_reports import save_health_report,read_saved_report
    report=run({'status':'missing_dependency','results':[], 'issue_scope':'local',
                'requirement_kind':'companion','reason':'Companion pairing required'})
    provider=report['providers'][0]
    assert report['status']=='incomplete'
    assert provider['status']=='inconclusive'
    assert provider['stages'][0]['status']=='not_verified'
    assert provider['stages'][0]['issue_scope']=='local'
    assert provider['local_issues'][0]['kind']=='companion'
    run_id=save_health_report(tmp_path,report)
    data=json.loads((tmp_path/run_id/'report.json').read_text())
    assert data['providers'][0]['local_issues'][0]['kind']=='companion'
    text=read_saved_report(tmp_path,run_id)
    assert 'Local requirements' in text and 'not a provider failure' in text


def test_legacy_missing_dependency_outcome_also_has_local_scope():
    report=run({'status':'missing_dependency','results':[],'reason':'Required package unavailable'})
    assert report['providers'][0]['status']=='inconclusive'
    assert report['providers'][0]['local_issues']


def test_companion_unavailable_survives_senshi_webview_error_boundary():
    from media_cli.extractors import senshi_webview
    client=SimpleNamespace(session=Mock(side_effect=CompanionUnavailable('Companion not running')))
    with pytest.raises(ProviderDependencyError):
        senshi_webview.resolve(123,client=client,timeout=1)


@pytest.mark.parametrize('returncode,stdout,stderr,expected', [
    (127,'','node: error while loading shared libraries: libsimdjson.so.34: cannot open shared object file','libsimdjson.so.34'),
    (1,'','unknown local startup failure','could not start'),
    (0,'v25.0.0\n','','26 or newer'),
])
def test_node_startup_failure_is_not_mislabeled_as_old_version(monkeypatch, returncode, stdout, stderr, expected):
    from media_cli.extractors import senshi_runtime as runtime
    monkeypatch.setattr(runtime,'is_termux',lambda:False)
    monkeypatch.setattr(runtime,'_linux_sandbox_tools',lambda:('bwrap','prlimit'))
    monkeypatch.setattr(runtime,'require_executable',lambda *args:'/usr/bin/node')
    monkeypatch.setattr(runtime.subprocess,'run',lambda *args,**kwargs:SimpleNamespace(returncode=returncode,stdout=stdout,stderr=stderr))
    with pytest.raises(ProviderDependencyError) as exc:
        runtime.resolve_vidcloud(123)
    assert expected in str(exc.value)
    if returncode:
        assert 'Upgrade Node.js' not in str(exc.value)


def test_failed_companion_wake_is_typed_as_local_unavailability(monkeypatch):
    from media_cli.services.android_webview import client
    monkeypatch.setattr(client.sys,'platform','android')
    monkeypatch.setattr(client.subprocess,'run',lambda *args,**kwargs:SimpleNamespace(returncode=1,stdout='Error: companion receiver not found',stderr=''))
    with pytest.raises(CompanionUnavailable):
        client.ProbeClient()._wake('f'*64)
