from types import SimpleNamespace
from media_cli.app.playback_episodes import _display_episode_label
from media_cli.app.episode_menu_view import EpisodeMenuOption, build_episode_menu_view
from media_cli.core.terminal import strip_ansi


FIRST='Oshi.no.Ko.S03E01.1080p.Opus.2.0.x265.mkv'
SECOND='Oshi.no.Ko.S03E02.1080p.Opus.2.0.x265.mkv'


def show():
    return {'_id':'a'*40,'_provider':'torrent','name':'Batch','_episode_labels':{'2':FIRST,'4':SECOND},
            '_torrent':{'name':'Batch','source':'nyaa','size_bytes':1_000_000_000,'seeders':10,'leechers':2}}


def test_file_labels_do_not_parse_audio_channels_as_episode_numbers():
    assert _display_episode_label(show(),'2','sub') == FIRST
    assert _display_episode_label(show(),'4','sub') == SECOND


def test_torrent_file_view_inherits_five_release_lines_without_episode_metadata():
    view=build_episode_menu_view(show(),[EpisodeMenuOption('2',FIRST)],view_id='files',image_key='batch',ttype='sub')
    assert view.prompt == 'Select file' and view.screen_title.endswith('Files')
    assert view.image is None and len(view.info_lines)==5
    assert 'trust unknown' in view.info_lines[1].text
    assert not any('EP 1' in line.text or 'GiB' in line.text for line in view.info_lines)


def test_download_picker_uses_filenames_and_queues_original_ids(monkeypatch):
    from media_cli.app import playback_menu as menu
    calls=[]
    queued=[]
    monkeypatch.setattr(menu.app_core,'ensure_episode_ids',lambda *args:['2','4'])
    def picker(flags,ui,prompt,options,**kwargs):
        calls.append((prompt,options,kwargs))
        if len(calls)==1:
            assert options == ['Download Selected File','Download All Files','Pick Files to Download…']
            assert kwargs['hints'][options[0]] == FIRST
            return 2
        assert options == [FIRST,SECOND]
        chrome=kwargs['chrome_fn']()
        lines=[strip_ansi(line) for line in chrome['info_fn'](1,120)]
        assert '1 GB' in lines[2] and lines[-1] == SECOND
        return [1]
    monkeypatch.setattr(menu,'tui_pick',picker)
    monkeypatch.setattr(menu,'_execute_batch_download',lambda *args:queued.extend(args[-1]))
    result=menu.handle_download_menu_state(SimpleNamespace(),SimpleNamespace(ui_show_ctx=show()),
        SimpleNamespace(current_ep_index=0),{},SimpleNamespace(),'sub')
    assert result=='DETAILS' and queued==['4']
    assert calls[1][0]=='Select files to download'


def test_download_submenu_propagates_manager_quit(monkeypatch):
    from media_cli.app import playback_menu as menu
    monkeypatch.setattr(menu.app_core, 'ensure_episode_ids', lambda *args: ['2', '4'])
    monkeypatch.setattr(menu, 'tui_pick', lambda *args, **kw: 0)
    monkeypatch.setattr(menu, '_execute_batch_download', lambda *args: 'QUIT')
    assert menu.handle_download_menu_state(SimpleNamespace(), SimpleNamespace(ui_show_ctx=show()),
        SimpleNamespace(current_ep_index=0), {}, SimpleNamespace(), 'sub') == 'QUIT'
