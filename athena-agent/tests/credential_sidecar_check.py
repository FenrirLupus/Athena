"""The credential sidecar: can the server make a signed provider call without ever holding the key?

Proved against a **fake upstream**, so nothing real is spent and the key used is a fake one.

The three assertions that matter:

  1. the upstream receives the right key, so the call actually works;
  2. the *server-side* request carries no Authorization header at all;
  3. `providers.base.api_key` is never called while the sidecar is in use — patched to raise, and the call still
     succeeds, which is the only proof that the key was not read in this process.

Plus the proxy's own guards: a wrong proxy token is refused, and a host that is not on the allowlist is refused.
"""
import json
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import credproxy
import providers.base as provider_base
from providers.openai_compat import OpenAICompatProvider

checks = {}
CAPTURED = {}


class Upstream(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):                                # noqa: N802
        length = int(self.headers.get("content-length") or 0)
        body = self.rfile.read(length) if length else b"{}"
        CAPTURED["authorization"] = self.headers.get("authorization") or ""
        CAPTURED["path"] = self.path
        CAPTURED["model"] = json.loads(body or b"{}").get("model")
        payload = json.dumps({"choices": [{"message": {"role": "assistant", "content": "pong"}}],
                              "usage": {"prompt_tokens": 1, "completion_tokens": 1}}).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


with tempfile.TemporaryDirectory() as td:
    athena = Path(td) / ".athena"
    (athena / "security").mkdir(parents=True)
    (athena / ".secret").write_text("DEEPSEEK_API_KEY=fake-key-123\n", encoding="utf-8")

    # the fake upstream stands in for api.deepseek.com
    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    upstream_port = upstream.server_address[1]
    threading.Thread(target=upstream.serve_forever, daemon=True).start()

    # provider.json tells the proxy which key belongs to which host
    (athena / "provider.json").write_text(json.dumps({
        "deepseek": {"base_url": f"http://127.0.0.1:{upstream_port}/v1", "format": "openai",
                     "keys": [{"agent": "", "name": "DEEPSEEK_API_KEY", "scope": "global"}]}}), encoding="utf-8")

    proxy = subprocess.Popen([sys.executable, "-m", "core.credproxy", "--athena", str(athena),
                              "--allow", "127.0.0.1"],
                             cwd=str(__import__("pathlib").Path(__file__).resolve().parents[1]), stdout=subprocess.PIPE, text=True)
    info = json.loads(proxy.stdout.readline() or "{}")
    checks["proxy_started"] = bool(info.get("port")) and bool(info.get("token"))
    checks["proxy_maps_host_to_key"] = info.get("providers", {}).get("127.0.0.1") == "DEEPSEEK_API_KEY"

    # 3 · make reading the key locally impossible, then place a real call
    original_api_key = provider_base.api_key

    def forbidden(*args, **kwargs):
        raise AssertionError("the server process read a credential while the sidecar was supposed to own it")

    provider_base.api_key = forbidden
    provider_base.PROXY.update({"url": f"http://127.0.0.1:{info['port']}", "token": info["token"]})
    try:
        provider = OpenAICompatProvider(base_url=f"http://127.0.0.1:{upstream_port}/v1", model="fake-model",
                                        key_name="DEEPSEEK_API_KEY", secret_root=athena)
        url, headers, payload = provider.build_request([{"role": "user", "content": "ping"}])
        checks["request_carries_no_authorization"] = "Authorization" not in headers and "authorization" not in headers
        checks["request_names_the_upstream"] = headers.get("X-Athena-Upstream", "").endswith("/v1/chat/completions")
        checks["request_goes_to_the_proxy"] = str(info["port"]) in url

        result = provider.call([{"role": "user", "content": "ping"}])
        checks["call_succeeds_through_the_proxy"] = "pong" in json.dumps(result)
        checks["upstream_saw_the_key"] = CAPTURED.get("authorization") == "Bearer fake-key-123"
        checks["upstream_path_is_correct"] = CAPTURED.get("path") == "/v1/chat/completions"
        checks["model_reached_the_upstream"] = CAPTURED.get("model") == "fake-model"
    finally:
        provider_base.api_key = original_api_key
        provider_base.PROXY.update({"url": "", "token": ""})

    # the proxy refuses a caller without the token, and a host off the allowlist
    import urllib.error
    import urllib.request

    def probe(headers, upstream_url=f"http://127.0.0.1:{upstream_port}/v1/chat/completions"):
        request = urllib.request.Request(f"http://127.0.0.1:{info['port']}/forward", data=b"{}", method="POST",
                                         headers={"content-type": "application/json",
                                                  "x-athena-upstream": upstream_url, **headers})
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status
        except urllib.error.HTTPError as exc:
            return exc.code

    checks["refuses_without_token"] = probe({}) == 401
    checks["refuses_wrong_token"] = probe({"x-athena-proxy": "nope"}) == 401
    checks["refuses_off_allowlist"] = probe({"x-athena-proxy": info["token"]},
                                            "https://evil.example.com/v1/chat/completions") == 403
    checks["allows_loopback_upstream"] = probe({"x-athena-proxy": info["token"]}) == 200

    proxy.terminate()
    upstream.shutdown()

for name, passed in checks.items():
    print(f"  {'PASS' if passed else 'FAIL'}  {name}")
print("\nRESULT:", "PASS" if all(checks.values()) else "FAIL")
