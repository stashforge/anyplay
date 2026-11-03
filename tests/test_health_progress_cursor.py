"""Health progress owns cursor visibility only while its check is active."""
import io
import pytest


class Terminal(io.StringIO):
    def isatty(self):return True


def setup(monkeypatch,tmp_path,terminal=True):
    from media_cli.cli import health
    from media_cli.health import catalogue
    output=Terminal() if terminal else io.StringIO()
    monkeypatch.setattr(health.sys,'stderr',output)
    monkeypatch.setattr(health.health_reports,'reports_directory',lambda:tmp_path)
    return health,catalogue,output


def test_cursor_hidden_during_progress_restored_before_report(monkeypatch,tmp_path):
    command,catalogue,output=setup(monkeypatch,tmp_path)
    def check(*args,**kwargs):
        kwargs['progress'](1,10,'Naruto')
        assert '\x1b[?25l' in output.getvalue()
        assert '\x1b[?25h' not in output.getvalue()
        return {'started_at':'2026-10-29T10:00:00Z','status':'complete','verification':'extraction','providers':[]}
    monkeypatch.setattr(catalogue,'check_catalogue',check)
    def show(*args,**kwargs):
        assert output.getvalue().endswith('\x1b[?25h\n')
        return 0
    monkeypatch.setattr(command,'show_report',show)
    assert command.handle_health_command('senshi',verify='extraction')==0


@pytest.mark.parametrize('error',[KeyboardInterrupt,RuntimeError])
def test_cursor_restored_on_interrupt_or_error(monkeypatch,tmp_path,error):
    command,catalogue,output=setup(monkeypatch,tmp_path)
    monkeypatch.setattr(catalogue,'check_catalogue',lambda *a,**k:(_ for _ in ()).throw(error()))
    with pytest.raises(error):command.handle_health_command('senshi',verify='extraction')
    assert output.getvalue().endswith('\x1b[?25h\n')


def test_redirected_progress_has_no_cursor_control_codes(monkeypatch,tmp_path):
    command,catalogue,output=setup(monkeypatch,tmp_path,terminal=False)
    def check(*a,**kw):
        kw['progress'](1,10,'Naruto')
        raise RuntimeError
    monkeypatch.setattr(catalogue,'check_catalogue',check)
    with pytest.raises(RuntimeError):command.handle_health_command('senshi',verify='extraction')
    assert '\x1b' not in output.getvalue()


def test_pager_cursor_hidden_until_exit(monkeypatch):
    from media_cli.cli import health
    from types import SimpleNamespace
    output=Terminal()
    monkeypatch.setattr(health.sys,'stdout',output)
    monkeypatch.setattr(health.sys,'stdin',Terminal())
    def pager(*a,**kw):
        assert output.getvalue()=='\x1b[?25l'
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(health.subprocess,'run',pager)
    assert health.show_report('Report\n')==0
    assert output.getvalue()=='\x1b[?25l\x1b[?25h'


@pytest.mark.parametrize('error',[KeyboardInterrupt,RuntimeError,OSError])
def test_pager_restores_cursor_on_error_or_interrupt(monkeypatch,error):
    from media_cli.cli import health
    output=Terminal()
    monkeypatch.setattr(health.sys,'stdout',output)
    monkeypatch.setattr(health.sys,'stdin',Terminal())
    monkeypatch.setattr(health.subprocess,'run',lambda *a,**k:(_ for _ in ()).throw(error()))
    if error is OSError:
        assert health.show_report('Report\n')==0
        assert output.getvalue().index('\x1b[?25h')<output.getvalue().index('Report')
    else:
        with pytest.raises(error):health.show_report('Report\n')
        assert output.getvalue().endswith('\x1b[?25h')
