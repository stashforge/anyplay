import os
import time

from test_ui_view_terminal import Terminal


def script(gate, calls):
    return f"""
from pathlib import Path
from media_cli import app_core
from media_cli.app.details import handle_details_state
from media_cli.context import CliFlags,MachineState,UiState
flags=CliFlags(incognito_mode=True,show_image=False)
show=dict(_id='one',_provider='example',name='Pending title',description='Description',genres=['Drama'])
ui=UiState(ui_show_ctx=show,ui_ttype_ctx='sub',search_prev_state='SEARCH')
ms=MachineState(show_id='one',show_title='Pending title',current_ep='1',just_picked_anime=True)
args=SimpleNamespace()
def ensure(snapshot,ttype,**kw):
    path=Path({str(calls)!r})
    path.write_text(str(int(path.read_text())+1) if path.exists() else '1')
    while not Path({str(gate)!r}).exists():
        time.sleep(.01)
    snapshot.update(name='Catalog ready',_episode_ids=['1','2'],_episode_ids_ttype=ttype,_episode_catalog_state='loaded')
    return ['1','2']
app_core.ensure_episode_ids=ensure
app_core.enrich_show_if_missing=lambda *a,**kw:None
app_core.prepare_show_display_state=lambda *a,**kw:None
app_core.get_local_progress=lambda *a:0
app_core.get_resume_time=lambda *a:0
app_core.get_show_anilist_id=lambda *a:None
app_core.is_confirmed_offline=lambda:False
app_core.make_single_show_info_fn=lambda *a:(lambda *a:None)
for _ in range(5):
    result=handle_details_state(flags,ui,ms,{{}},args,'sub',lambda *a:False)
    if result!='DETAILS':break
finish(dict(state=result,episode=ms.current_ep))
"""


def test_details_opens_while_catalog_is_blocked_and_blocks_playback_only(tmp_path):
    calls, gate = tmp_path/'calls', tmp_path/'release'
    with Terminal(tmp_path, script(gate,calls)) as terminal:
        terminal.send_after(b'Select action', b'Mirrors\r')
        terminal.until(b'Episodes are still loading')
        assert calls.read_text() == '1'
        os.write(terminal.master, b'\x1b')
        assert terminal.result()['state'] == 'SEARCH'


def test_loading_footer_animates_with_configured_spinner(tmp_path):
    code = script(tmp_path/'release',tmp_path/'calls').replace(
        'ms,{},args', "ms,{'spinner':'line'},args")
    with Terminal(tmp_path,code) as terminal:
        terminal.until(b'| Loading SUB episodes')
        terminal.until(b'/ Loading SUB episodes')
        os.write(terminal.master,b'\x1b')
        assert terminal.result()['state'] == 'SEARCH'


def test_ready_catalog_rebuilds_details_without_another_provider_fetch(tmp_path):
    calls, gate = tmp_path/'calls', tmp_path/'release'
    with Terminal(tmp_path, script(gate,calls)) as terminal:
        terminal.until(b'Select action')
        gate.touch()
        terminal.send_after(b'Catalog ready', b'Play\r')
        assert terminal.result() == {'state':'PLAY','episode':'1'}
        assert calls.read_text() == '1'


def test_failed_catalog_survives_settings_return_and_retries_only_explicitly(tmp_path):
    calls, gate = tmp_path/'calls', tmp_path/'release'
    gate.touch()
    code = script(gate,calls).replace("snapshot.update(name=", "raise TimeoutError('Lookup failed')\n    snapshot.update(name=")
    with Terminal(tmp_path, code) as terminal:
        terminal.send_after(b'Retry Episodes', b'Playback\r')
        terminal.send_after(b'Auto-Delete Buffer', b'\x1b')
        terminal.until(b'Select action')
        assert calls.read_text() == '1'
        # Clear the restored Playback filter, then explicitly retry the catalog.
        os.write(terminal.master, b'\x7f'*8 + b'Retry\r')
        deadline = time.monotonic() + 3
        while calls.read_text() != '2' and time.monotonic() < deadline:
            time.sleep(.01)
        assert calls.read_text() == '2'
        os.write(terminal.master, b'\x1b')
        assert terminal.result()['state'] == 'SEARCH'


def test_explicit_refresh_survives_history_style_runtime_field_stripping(tmp_path):
    calls, gate = tmp_path/'calls', tmp_path/'release'
    gate.touch()
    code = script(gate,calls).replace(
        'for _ in range(5):',
        "def refresh(entry,**kw):\n"
        "    entry['show'].update(name='Refreshed catalog',_episode_ids=['1','2','3'],"
        "_episode_ids_by_ttype={'sub':['1','2','3']},_episode_catalog_state='loaded')\n"
        "app_core.refresh_history_entry_provider_catalog=refresh\n"
        "reopened=False\nfor _ in range(5):",
    ).replace(
        "    if result!='DETAILS':break",
        "    if result=='DETAILS' and show.get('name')=='Refreshed catalog' and not reopened:\n"
        "        reopened=True\n"
        "        for key in ('_episode_ids','_episode_ids_by_ttype','_episode_ids_ttype'):\n"
        "            show.pop(key,None)\n"
        "    if result!='DETAILS':break",
    ).replace('episode=ms.current_ep)', 'episode=ms.current_ep,total=ms.total_eps)')
    with Terminal(tmp_path,code) as terminal:
        terminal.send_after(b'Catalog ready',b'\x12')
        terminal.send_after(b'Refreshed catalog',b'Play\r')
        assert terminal.result() == {'state':'PLAY','episode':'1','total':3}
        assert calls.read_text() == '1'
