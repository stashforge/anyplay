from concurrent.futures import Future

from media_cli.app.details_preparation import PreparedDetails


class Executor:
    def __init__(self):
        self.calls = []

    def submit(self, fn, *args):
        future = Future()
        self.calls.append((future, fn, args))
        return future


def test_jobs_do_not_wait_or_mutate_live_show_and_reuse_across_returns():
    from media_cli.app.details_catalog import DetailsCatalogJobs
    executor = Executor()
    jobs = DetailsCatalogJobs(executor=executor)
    show = {'_provider':'one', '_id':'same', 'name':'First'}
    assert jobs.read('session', show, 'sub').state == 'loading'
    assert jobs.read('session', dict(show), 'sub').state == 'loading'
    assert len(executor.calls) == 1
    future, _, args = executor.calls[0]
    assert args[0] is not show
    args[0]['name'] = 'Detached'
    assert show['name'] == 'First'
    future.set_result(PreparedDetails({'_episode_ids':['a']}, ('a',), {}))
    assert jobs.read('session', show, 'sub').state == 'loaded'
    assert jobs.read('session', show, 'sub').result.episode_ids == ('a',)
    assert len(executor.calls) == 1


def test_failure_is_retained_until_explicit_retry_and_late_result_is_isolated():
    from media_cli.app.details_catalog import DetailsCatalogJobs
    executor = Executor()
    jobs = DetailsCatalogJobs(executor=executor)
    show = {'_provider':'one', '_id':'same'}
    jobs.read('session', show, 'sub')
    executor.calls[0][0].set_exception(TimeoutError('Site timed out'))
    assert jobs.read('session', show, 'sub').state == 'failed'
    assert jobs.read('session', show, 'sub').state == 'failed'
    assert len(executor.calls) == 1
    jobs.retry('session', show, 'sub')
    assert jobs.read('session', show, 'sub').state == 'loading'
    assert len(executor.calls) == 2


def test_provider_language_and_cli_session_are_distinct_keys():
    from media_cli.app.details_catalog import DetailsCatalogJobs
    executor = Executor()
    jobs = DetailsCatalogJobs(executor=executor)
    for session, provider, language in (
        ('a','one','sub'), ('a','two','sub'), ('a','one','dub'), ('b','one','sub'),
    ):
        jobs.read(session, {'_provider':provider, '_id':'same'}, language)
    assert len(executor.calls) == 4


def test_retry_does_not_accept_an_abandoned_generation():
    from media_cli.app.details_catalog import DetailsCatalogJobs
    executor = Executor()
    jobs = DetailsCatalogJobs(executor=executor)
    show = {'_provider':'one', '_id':'same'}
    jobs.read('session', show, 'sub')
    old = executor.calls[0][0]
    old.set_running_or_notify_cancel()
    jobs.retry('session', show, 'sub')
    jobs.read('session', show, 'sub')
    old.set_result(PreparedDetails({}, ('old',), {}))
    assert jobs.read('session', show, 'sub').state == 'loading'


def test_accepting_late_language_preserves_other_language_catalog():
    from media_cli.app.details_preparation import commit_details_transition
    before = {'_provider':'one','_id':'same','_episode_ids_by_ttype':{}}
    result = PreparedDetails({**before,'_episode_ids_by_ttype':{'sub':['sub1']}},('sub1',),before)
    live = {**before,'_episode_ids_by_ttype':{'dub':['dub1']}}
    commit_details_transition(live,result)
    assert live['_episode_ids_by_ttype'] == {'sub':['sub1'],'dub':['dub1']}


def test_explicit_refresh_replaces_cached_result_even_when_it_fails():
    from media_cli.app.details_catalog import DetailsCatalogJobs
    executor = Executor()
    jobs = DetailsCatalogJobs(executor=executor)
    show = {'_provider':'one','_id':'same'}
    jobs.read('session',show,'sub')
    executor.calls[0][0].set_result(PreparedDetails({},('old',),{}))
    refreshed = PreparedDetails({'_episode_catalog_error':'Refresh failed'},(),{})
    jobs.publish('session',show,'sub',refreshed)
    assert jobs.read('session',show,'sub').state == 'failed'
    assert jobs.read('session',show,'sub').result.episode_ids == ()
    assert len(executor.calls) == 1


def test_failed_sub_result_never_relabels_active_dub_ids_as_sub(monkeypatch):
    from types import SimpleNamespace

    from media_cli import app_core
    from media_cli.app import details, details_catalog, playback_menu
    from media_cli.context import CliFlags, MachineState, UiState

    original = {'_id':'one','_provider':'example','name':'Title'}
    failed = PreparedDetails({**original,'_episode_ids_ttype':'sub',
        '_episode_catalog_state':'unavailable','_episode_catalog_error':'No Sub episodes'},(),original)
    live = {**original,'_episode_ids_ttype':'dub','_episode_ids':['dub1'],
            '_episode_ids_by_ttype':{'dub':['dub1']}}
    ui=UiState(ui_show_ctx=live,ui_ttype_ctx='sub',search_prev_state='SEARCH')
    monkeypatch.setattr(details_catalog,'interactive_catalog',lambda *a:True)
    monkeypatch.setattr(details_catalog.catalog_jobs,'read',lambda *a:details_catalog.CatalogStatus('failed',failed,'No Sub episodes'))
    monkeypatch.setattr(app_core,'with_loading',lambda msg,fn,*a,**kw:fn(*a,**kw))
    monkeypatch.setattr(app_core,'prepare_show_display_state',lambda *a,**kw:None)
    monkeypatch.setattr(app_core,'get_show_anilist_id',lambda *a:None)
    monkeypatch.setattr(app_core,'get_resume_time',lambda *a:0)
    observed=[]
    monkeypatch.setattr(playback_menu,'handle_action_menu_state',lambda *a,**kw:observed.append(kw['ready_ids']) or 'DETAILS')
    details.handle_details_state(CliFlags(incognito_mode=True),ui,
        MachineState(show_id='one',current_ep='dub1'),{},SimpleNamespace(),'sub',lambda *a:False)
    assert observed == [()]
    assert live['_episode_ids'] == []
    assert live['_episode_ids_by_ttype']['dub'] == ['dub1']
