"""Senshi WebCrypto runtime through the shared Android companion transport."""

import base64
import json
import re
import time

from ..services.android_webview.client import CompanionSetupRequired, ProbeClient, ProbeError
from ..services.android_webview.requirements import companion_dependency_error
from ..services.http import UA
from ..services.network_affinity import ipv4_curl_options


def browser_program(runtime: str, remote_id: int | str, user_agent: str) -> str:
    if not isinstance(runtime, str) or len(runtime.encode("utf-8")) > 524288:
        raise ProbeError("Senshi runtime too large")
    if isinstance(remote_id, bool) or not re.fullmatch(r"[1-9][0-9]{0,11}", str(remote_id)):
        raise ProbeError("Invalid Senshi source ID")
    encoded = base64.b64encode(runtime.encode("utf-8")).decode("ascii")
    program = r"""(() => {
      const source = new TextDecoder().decode(Uint8Array.from(atob(RUNTIME), c=>c.charCodeAt(0)));
      const scope = {URL,URLSearchParams,TextEncoder,TextDecoder,Uint8Array,ArrayBuffer,
        crypto,atob,btoa,fetch,Headers,Request,Response,setTimeout,clearTimeout,
        setInterval:()=>0,clearInterval:()=>{},performance:{now:()=>1000},
        console:{log(){},warn(){},error(){}}};
      scope.window=scope; scope.self=scope;
      scope.navigator={userAgent:USER_AGENT,language:'en-US',languages:['en-US']};
      scope.location={origin:'https://senshi.to',href:'https://senshi.to/',hostname:'senshi.to',protocol:'https:'};
      scope.document={cookie:'',referrer:'',currentScript:null,querySelector:()=>null,
        addEventListener(){},createElement:()=>({style:{},getContext:()=>null,addEventListener(){}}),
        head:{appendChild(){}},documentElement:{}};
      const names=Object.keys(scope);
      return new Function(...names, source+'\nreturn window.__oct.open(SOURCE_ID);')
        .call(scope,...names.map(k=>scope[k]));
    })()"""
    return (
        program.replace("SOURCE_ID", str(int(remote_id)))
        .replace("USER_AGENT", json.dumps(user_agent))
        .replace("RUNTIME", json.dumps(encoded))
    )


def resolve(remote_id, *, deadline=None, timeout=30, cancel=None, client=None):
    from curl_cffi.requests import Session

    from .senshi_runtime import SenshiRuntimeError, _check, _fetch_runtime, _request

    if isinstance(remote_id, bool) or not re.fullmatch(r"[1-9][0-9]{0,11}", str(remote_id)):
        raise SenshiRuntimeError("Invalid Senshi episode source ID")
    if deadline is None:
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 60:
            raise SenshiRuntimeError("Invalid Senshi extraction timeout")
        deadline = time.monotonic() + timeout
    client = client if client is not None else ProbeClient()
    try:
        for attempt in range(2):
            _check(deadline, cancel)
            with client.session("senshi", deadline=deadline, cancel=cancel) as sid:
                with Session(impersonate="chrome", trust_env=False, curl_options=ipv4_curl_options()) as session:
                    runtime = _fetch_runtime(session, deadline, cancel)
                    program = browser_program(runtime, remote_id, UA)
                    job = client.run_mediated(
                        sid, program, lambda request: _request(request, session, deadline, cancel), deadline, cancel
                    )
                    _check(deadline, cancel)
                    if job.get("status") != "ok":
                        if job.get("status") == "failed" and not attempt:
                            continue
                        raise SenshiRuntimeError("Senshi WebView runtime failed")
                    result = job.get("result")
                    if isinstance(result, dict):
                        result = [result]
                    if (
                        not isinstance(result, list)
                        or not 1 <= len(result) <= 16
                        or not all(isinstance(x, dict) for x in result)
                    ):
                        raise SenshiRuntimeError("Senshi returned no stream sources")
                    return result
    except CompanionSetupRequired as error:
        _check(deadline, cancel)
        raise companion_dependency_error(error) from None
    except ProbeError as error:
        _check(deadline, cancel)
        # Companion exceptions contain fixed local diagnostics, never upstream bodies.
        raise SenshiRuntimeError(str(error)) from None
    raise SenshiRuntimeError("Senshi WebView runtime failed")
