"""Readable reports retain evidence without mixing stages or losing narrow text."""
from copy import deepcopy

from media_cli.domain.health_report import format_health_report
from media_cli.core.terminal import display_width


def report():
    common={'title':'Dandadan','mal_id':57334,'provider_title_id':'native-title-123',
            'release_year':2024,'age_group':'recent'}
    stages=[
        {'name':'search','status':'passed','title':'Dandadan','reason':'Returned 3 titles'},
        {'name':'title_details','status':'passed',**common,'present_fields':['title','cover'],
         'missing_fields':['description'],'elapsed_seconds':1.25},
        {'name':'source_resolution','status':'passed',**common,'episode':'12','source_count':2,
         'source_names':['HD-1','HD-2'],'mirror_count':1,'unresolved_count':1,
         'mirrors':[{'resolution':'1080p','type':'hls','host':'cdn.example','source':'HD-1',
                     'channel':'Sub','separate_audio':False,'subtitle_count':1,'headers_present':True}]},
        {'name':'media_access','status':'inconclusive',**common,'episode':'12','track':'video',
         'mirror':'1080p','mirror_index':0,'host':'cdn.example','required':True,
         'http_status':503,'bytes_sampled':24,'elapsed_seconds':2.5,
         'reason':'A slow response is not proof of a dead mirror.'},
        {'name':'media_access','status':'not_verified',**common,'episode':'12','track':'audio',
         'required':False,'reason':'No separate track supplied'},
        {'name':'playback','status':'not_verified','reason':'Not checked in this HTTP run'},
    ]
    return {'run_id':'report-example','started_at':'2026-10-29T10:00:00+00:00',
            'saved_at':'2026-10-29T10:01:00+00:00','status':'complete','verification':'http',
            'seed':42,'providers':[{'id':'example','name':'Example','status':'inconclusive','stages':stages}]}


def test_summary_findings_titles_and_run_information_have_clear_order():
    text=format_health_report(report())
    assert text.index('CHECK SUMMARY') < text.index('FINDINGS') < text.index('TITLE DETAILS') < text.index('RUN INFORMATION')
    assert '\n\nVIDEO ACCESS\n' in text and '\n\nAUDIO ACCESS\n' in text
    assert 'Video access' in text and 'Inconclusive' in text
    assert 'HTTP 503' in text or 'HTTP status' in text


def test_complete_evidence_survives_with_common_ids_shown_once():
    data=report();before=deepcopy(data);text=format_health_report(data)
    assert data==before
    for value in ('57334','native-title-123','2024','recent','HD-1','HD-2','1080p','hls',
                  'cdn.example','Sub','503','24','1.25','2.50','description','cover','report-example','42'):
        assert value in text,value
    assert text.count('native-title-123')==1
    assert 'Not required' in text and 'Unverified' in text
    assert 'Not checked in this HTTP run' in text


def test_conflicting_reference_values_are_not_silently_deduplicated():
    data=report();data['providers'][0]['stages'][2]['provider_title_id']='different-native-id'
    text=format_health_report(data)
    assert 'different-native-id' in text and 'native-title-123' in text


def test_narrow_unicode_wrapping_loses_no_words_or_id_characters():
    data=report();name='斗罗大陆 第二季 很长的标题'
    for stage in data['providers'][0]['stages']:
        if stage.get('title'):stage['title']=name
    data['providers'][0]['stages'][0]['reason']='word '*30
    text=format_health_report(data,width=32)
    assert all(display_width(line)<=32 for line in text.splitlines())
    assert 'native-title-123' in ''.join(text.split())
    assert ''.join(name.split()) in ''.join(text.split())
    assert text.count('word')==30


def test_failed_uncertain_untested_and_not_applicable_stay_distinct():
    data=report();data['providers'][0]['stages'].extend([
        {'name':'mirror_extraction','status':'failed','reason':'Confirmed failure'},
        {'name':'source_discovery','status':'not_applicable','reason':'Combined operation'},
        {'name':'source_coverage','status':'skipped','reason':'Not selected'},
    ])
    text=format_health_report(data)
    assert all(label in text for label in ('Failed','Inconclusive','Unverified','Not applicable','Skipped','Not required'))


def test_existing_structured_report_gets_new_layout_without_rewriting_files(tmp_path):
    import json
    from media_cli.state.health_reports import read_saved_report
    data=report();data['schema_version']=1
    folder=tmp_path/data['run_id'];folder.mkdir()
    (folder/'report.json').write_text(json.dumps(data))
    (folder/'report.txt').write_text('OLD FLAT REPORT\n')
    before={p:p.read_bytes() for p in folder.iterdir()}
    text=read_saved_report(tmp_path,data['run_id'])
    assert 'CHECK SUMMARY' in text and 'native-title-123' in text
    assert all(p.read_bytes()==value for p,value in before.items())


def test_multiple_providers_keep_their_results_separate():
    data = report()
    second = deepcopy(data['providers'][0])
    second.update(id='other', name='Other', status='missing_dependency')
    second['stages'] = []
    second['local_issues'] = [dict(kind='runtime', stage='extraction', reason='Node.js unavailable')]
    data['providers'].append(second)
    text = format_health_report(data)
    assert text.count('CHECK SUMMARY') == 2
    assert text.index('Example (example)') < text.index('Other (other)')
    assert 'Local requirements — not a provider failure:' in text
    assert 'Node.js unavailable' in text and 'No stages were checked.' in text


def test_invalid_structured_evidence_keeps_legacy_text_readable(tmp_path):
    import json
    from media_cli.state.health_reports import read_saved_report
    data = report()
    data.update(schema_version=1)
    data['providers'][0]['stages'][0]['status'] = 'invalid'
    folder = tmp_path / data['run_id']
    folder.mkdir()
    (folder / 'report.json').write_text(json.dumps(data))
    (folder / 'report.txt').write_text('Legacy evidence\n')
    assert read_saved_report(tmp_path, data['run_id']) == 'Legacy evidence\n'


def test_existing_structured_evidence_is_redacted_on_view(tmp_path):
    import json
    from media_cli.state.health_reports import read_saved_report
    data = report()
    data.update(schema_version=1)
    data['providers'][0]['stages'][0]['reason'] = 'Failed https://cdn.example/private?token=SECRET Bearer PRIVATE'
    folder = tmp_path / data['run_id']
    folder.mkdir()
    (folder / 'report.json').write_text(json.dumps(data))
    (folder / 'report.txt').write_text('Old report\n')
    text = read_saved_report(tmp_path, data['run_id'])
    assert 'cdn.example' in text
    assert 'SECRET' not in text and 'PRIVATE' not in text and '/private' not in text


def test_desktop_summary_aligns_results_without_truncating():
    text = format_health_report(report(), width=80)
    summary = text.split('CHECK SUMMARY\n')[1].split('FINDINGS')[0]
    assert 'Video access' in summary and '1 Inconclusive' in summary
    rows = [line for line in summary.splitlines() if line.startswith(('Search ', 'Video access '))]
    assert rows[0].index('1') == rows[1].index('1')
    assert all(display_width(line) <= 80 for line in text.splitlines())
