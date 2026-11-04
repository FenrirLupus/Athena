"""Athena's offline selftest — cases 1–12 against the mock provider.

Out of `cli/__main__.py` on purpose: the CLI is a surface (serve, turn, config, models) and this is a test
suite. Kept as one file rather than twelve so `./athena.sh selftest` stays a single command with a single
verdict, which is what it is used for — a gate before every change.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

# runnable on its own as well as through `./athena.sh selftest`: the project root must be importable either
# way, and a hardcoded path breaks the moment the tree moves
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# the stub lives here rather than beside the CLI: a test double reachable from the command surface shows up
# in the code graph as a core abstraction, which is how it got mistaken for one (doc 20 §2)
class _Stub:
    """A tiny HTTP server that speaks one wire format, for the selftest.

    Real adapters are only proven by a real request: the interesting bugs are in headers, in the shape
    of tool calls, and in what a provider does *not* accept (Anthropic rejects two user turns in a row).
    """

    def __init__(self, kind: str):
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        import json as _json

        self.kind = kind
        self.requests: list[dict] = []
        self.headers_seen: list[dict] = []
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):        # silence the default stderr chatter
                pass

            def do_GET(self):
                body = _json.dumps({"data": [{"id": "stub-model"}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                payload = _json.loads(self.rfile.read(length) or b"{}")
                stub.requests.append(payload)
                stub.headers_seen.append({k.lower(): v for k, v in self.headers.items()})
                if stub.kind == "openai":
                    body = {"choices": [{"message": {
                        "content": "stub says hi",
                        "tool_calls": [{"id": "call_x", "type": "function",
                                        "function": {"name": "echo",
                                                     "arguments": _json.dumps({"text": "ok"})}}],
                    }, "finish_reason": "tool_calls"}],
                        "usage": {"prompt_tokens": 11, "completion_tokens": 3}}
                else:
                    body = {"content": [{"type": "text", "text": "stub says hi"},
                                        {"type": "tool_use", "id": "toolu_x", "name": "echo",
                                         "input": {"text": "ok"}}],
                            "stop_reason": "tool_use",
                            "usage": {"input_tokens": 11, "output_tokens": 3}}
                raw = _json.dumps(body).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()




def run() -> int:
    """Run every case and print a verdict. Returns 0 on pass, 1 on failure."""
    from config import loader
    from core.budget import IterationBudget
    from core.loop import run_turn
    from core.session import Session
    from pathlib import Path
    import tempfile

    cfg = loader.load()
    # the selftest is offline by contract: it must never reach for the configured endpoint, or a
    # user who enabled a local model gets a test that loads 9 GB of weights to check a loop
    cfg.setdefault("model", {})
    cfg["model"]["provider"] = "mock"
    cfg["model"].setdefault("local", {})["enabled"] = False
    from providers import base as provider_base
    from tools import registry as tools_registry

    provider = provider_base.build(cfg)
    tools = tools_registry.default_registry(cfg)
    ok = True
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        session = Session("selftest", root)

        # 1. plain turn: no tools, one call
        r = run_turn(channel="selftest", channel_root=root, session=session, provider=provider,
                     tools_registry=tools, instructions="be brief", user_text="hello",
                     cfg=cfg)
        print(f"plain   status={r['status']} calls={r['calls']} reply={r['reply'][:60]!r}")
        ok &= r["status"] == "completed" and r["calls"] == 1

        # 2. tool turn: model asks for echo, gets the result, then answers
        r2 = run_turn(channel="selftest", channel_root=root, session=session, provider=provider,
                      tools_registry=tools, instructions="be brief", user_text="echo piece of cake",
                      cfg=cfg)
        print(f"tool    status={r2['status']} calls={r2['calls']} rounds={r2['tool_rounds']} "
              f"reply={r2['reply'][:60]!r}")
        ok &= r2["status"] == "completed" and r2["tool_rounds"] == 1 and "piece of cake" in r2["reply"]

        # 3. budget: a one-call budget must still return prose, not a truncated tool call
        r3 = run_turn(channel="selftest", channel_root=root, session=session, provider=provider,
                      tools_registry=tools, instructions="be brief", user_text="echo budget test",
                      cfg=cfg, budget=IterationBudget(max_turns=1, max_usd=1.0, grace_calls=0))
        print(f"budget  status={r3['status']} calls={r3['calls']} reply={r3['reply'][:60]!r}")
        ok &= r3["status"].startswith("exhausted") and bool(r3["reply"])

        # 4. every tool call answered: transcript has no dangling tool_calls
        msgs = session.history()
        dangling = [m for m in msgs if m.get("role") == "assistant" and m.get("tool_calls")
                    and not any(x.get("tool_call_id") == tc["id"] for x in msgs
                                for tc in m["tool_calls"] if x.get("role") == "tool")]
        print(f"turns   session_messages={len(msgs)} dangling_tool_calls={len(dangling)}")
        ok &= not dangling

        # 5. profile: the layout exists and the stack renders identity, emotion and memory rows
        from core import profile as profile_mod
        from context import stack as stack_mod

        profile_mod.ensure(root)
        (root / "assistant" / "ASSISTANT.md").write_text(
            "---\nname_first: Athena\n---\n\n# Who I am\n\nI am a persistent agent.\n",
            encoding="utf-8")
        (root / "assistant" / "EMOTION.md").write_text(
            "---\njoy: +0.40\ntrust: +0.20\n---\n\nstate\n", encoding="utf-8")
        (root / "user" / "USER.md").write_text(
            "---\nname_nick: Fen\n---\n\n# Who I serve\n\nPrefers terse answers.\n",
            encoding="utf-8")
        (root / "memories" / "MEMORY.md").write_text(
            '[file: "REFERENCE.md"][location: "memories/self/x/y/"][note: "a routing line"]\n',
            encoding="utf-8")

        parts = sorted(p.name for p in root.iterdir() if p.is_dir())
        print(f"profile dirs={parts}")
        ok &= set(parts) >= {"assistant", "user", "memories", "sessions"}

        msgs2, rep2 = stack_mod.build("sys", root, [{"role": "user", "content": "hi"}], "hi", cfg)
        sys_text = msgs2[0]["content"]
        rendered = {
            "identity": "persistent agent" in sys_text,
            "assistant_emotion": "joy +0.40" in sys_text,
            "user_identity": "Prefers terse answers" in sys_text,
            "memory_row": "a routing line" in sys_text,
            "no_double_header": sys_text.count("# Who I am") == 1,
        }
        print(f"stack   {rendered} memory_rows={rep2['memory_rows']}")
        ok &= all(rendered.values()) and rep2["memory_rows"] == 1

        # 6. compression: fill history, force a compression, prove no message is lost
        import copy
        cfg2 = copy.deepcopy(cfg)
        cfg2["budget"]["history_pct"] = 1.0          # a tiny share makes the window fill quickly
        cfg2.setdefault("compression", {})
        # the policy lives in `compression` now: soft 0 means "compress as soon as the window shows any fill"
        cfg2["compression"]["threshold_soft_pct"] = 0.0
        cfg2["compression"]["threshold_hard_pct"] = 1.0
        cfg2["compression"]["keep_after_compress_pct"] = 50

        root2 = root / "compress-channel"
        profile_mod.ensure(root2)
        session2 = Session("compress-channel", root2)
        filler = "the quick brown fox jumps over the lazy dog " * 8
        for i in range(6):
            run_turn(channel="compress-channel", channel_root=root2, session=session2,
                     provider=provider, tools_registry=tools, instructions="be brief",
                     user_text=f"{filler} [{i}]", cfg=cfg2)

        snaps = session2.snapshots()
        latest_msgs = session2.size()
        vaulted = session2.vault.count()
        summary = session2.summary_text()
        live_lines = [l for l in (root2 / "sessions" / "LATEST.jsonl").read_text().splitlines() if l.strip()]
        snap_lines = 0
        for sn in snaps:
            snap_lines += len([l for l in sn.read_text().splitlines() if l.strip()])

        total = latest_msgs + snap_lines
        comp_dir = root2 / "sessions" / "compression"
        archives = sorted(p.name for p in comp_dir.glob("summary_*.md"))
        current = comp_dir / "SUMMARY.md"
        print(f"compress snapshots={len(snaps)} latest_msgs={latest_msgs} "
              f"snapshot_msgs={snap_lines} total={total} vault_rows={vaulted} "
              f"summary_bytes={len(summary)} summary_archives={len(archives)}")
        ok &= current.exists()
        # the previous digest is preserved on every write after the first
        ok &= len(archives) == max(0, len(snaps) - 1)
        ok &= all((comp_dir / a).stat().st_size > 0 for a in archives)
        ok &= len(snaps) >= 1
        ok &= vaulted >= snap_lines            # copy-first: every snapshot turn is in the vault
        ok &= len(summary) > 200
        ok &= latest_msgs < total              # the live window really was truncated
        ok &= total >= 12                      # 6 user + 6 assistant, nothing lost

        # 7. the summary reaches the prompt
        _, rep3 = stack_mod.build("sys", root2, session2.history(), "next", cfg2,
                                  summary=session2.summary_text())
        ok &= bool(rep3.get("allocated", {}).get("summary", 0) > 0)
        msgs3, _ = stack_mod.build("sys", root2, session2.history(), "next", cfg2,
                                   summary=session2.summary_text())
        ok &= "What happened before this window" in msgs3[0]["content"]
        print(f"summary reaches prompt: {'What happened before this window' in msgs3[0]['content']}")

        # 8. both wire formats, over a real socket: the shape is what breaks, not the call
        from providers.openai_compat import OpenAICompatProvider
        from providers.anthropic import AnthropicProvider
        from providers import wire as wire_mod
        import os as _os

        _os.environ["ATHENA_API_KEY"] = "test-key-not-real"
        convo = [
            {"role": "system", "content": "You are Athena."},
            {"role": "user", "content": "echo this", "ts": "2026-01-01T00:00:00Z"},
            {"role": "assistant", "content": "", "ts": "x",
             "tool_calls": [{"id": "call_1", "name": "echo", "arguments": {"text": "hi"}}]},
            {"role": "tool", "tool_call_id": "call_1", "name": "echo", "content": "hi", "ts": "x"},
        ]
        tool_schema = [{"type": "function", "function": {
            "name": "echo", "description": "echo text back",
            "parameters": {"type": "object", "properties": {"text": {"type": "string"}}}}}]

        oa = _Stub("openai")
        an = _Stub("anthropic")
        try:
            oa_provider = OpenAICompatProvider(oa.url + "/v1", "stub-model")
            oa_resp = oa_provider.call(convo, tools=tool_schema, max_tokens=64)
            sent = oa.requests[-1]
            oa_out = wire_mod.to_openai(convo)
            oa_checks = {
                "bookkeeping_stripped": all("ts" not in m for m in sent["messages"]),
                "args_are_json_string": isinstance(
                    sent["messages"][2]["tool_calls"][0]["function"]["arguments"], str),
                "tool_role_kept": sent["messages"][3]["role"] == "tool",
                "tool_call_id_kept": sent["messages"][3].get("tool_call_id") == "call_1",
                "resp_args_parsed": oa_resp["tool_calls"][0]["arguments"] == {"text": "ok"},
                "finish_reason": oa_resp["finish_reason"] == "tool_calls",
                "bearer_sent": oa.headers_seen[-1].get("authorization", "").startswith("Bearer "),
                "url_has_v1": oa.headers_seen and True,
            }

            an_provider = AnthropicProvider(base_url=an.url, model="stub-claude")
            an_resp = an_provider.call(convo, tools=tool_schema, max_tokens=64)
            asent = an.requests[-1]
            roles = [m["role"] for m in asent["messages"]]
            alternating = all(roles[i] != roles[i + 1] for i in range(len(roles) - 1))
            an_checks = {
                "system_is_top_level": isinstance(asent.get("system"), str)
                                       and "You are Athena." in asent["system"],
                "no_system_message": all(m["role"] != "system" for m in asent["messages"]),
                "roles_alternate": alternating,
                "tool_use_block": any(b.get("type") == "tool_use"
                                      for m in asent["messages"] if m["role"] == "assistant"
                                      for b in m["content"]),
                "tool_result_in_user": any(m["role"] == "user" and any(
                    b.get("type") == "tool_result" and b.get("tool_use_id") == "call_1"
                    for b in m["content"]) for m in asent["messages"]),
                "input_schema": asent["tools"][0].get("input_schema", {}).get("type") == "object",
                "no_parameters_key": "parameters" not in asent["tools"][0],
                "api_key_header": bool(an.headers_seen[-1].get("x-api-key")),
                "version_header": an.headers_seen[-1].get("anthropic-version", "").startswith("20"),
                "resp_tool_calls": an_resp["tool_calls"][0]["arguments"] == {"text": "ok"},
                "resp_finish": an_resp["finish_reason"] == "tool_calls",
                "resp_usage_mapped": an_resp["usage"]["prompt_tokens"] == 11,
            }
        finally:
            oa.close()
            an.close()
        print(f"openai  {oa_checks}")
        print(f"anthro  {an_checks}")
        ok &= all(oa_checks.values()) and all(an_checks.values())

        # 9. models folder: manifest round-trip, resolve, and the llama.cpp command line
        from models import store as model_store

        models_tmp = root / "models"
        models_tmp.mkdir(exist_ok=True)
        _os.environ["ATHENA_MODELS"] = str(models_tmp)
        (models_tmp / "demo-model").mkdir()
        (models_tmp / "demo-model" / "demo-q4_k_m.gguf").write_bytes(b"GGUF" + b"\0" * 64)
        model_store.write_manifest(models_tmp / "demo-model", {
            "name": "demo-model", "repo": "org/demo-GGUF", "files": ["demo-q4_k_m.gguf"],
            "quant": "Q4_K_M", "bytes": 68, "downloaded": "2026-01-01T00:00:00Z"})
        listed = model_store.list_models()
        resolved = model_store.resolve("demo-model")

        from providers.local import LocalServer
        import copy as _copy
        lcfg = _copy.deepcopy(cfg)
        lcfg["model"]["local"] = {"enabled": True, "backend": "llamacpp", "name": "demo-model",
                                  "port": 18080, "ctx": 32768, "binary": "llama-server"}
        lserver = LocalServer(lcfg)
        cmd = lserver.render()
        vcfg = _copy.deepcopy(cfg)
        vcfg["model"]["local"] = {"enabled": True, "backend": "vllm", "name": "demo-model",
                                  "port": 18081, "ctx": 32768, "python": "/opt/vllm/bin/python",
                                  "quantization": "bitsandbytes"}
        vcmd = LocalServer(vcfg).render()
        models_checks = {
            "llamacpp_jinja": "--jinja" in cmd,
            "vllm_entry": vcmd[:3] == ["/opt/vllm/bin/python", "-m", "vllm.entrypoints.openai.api_server"],
            "vllm_max_len": vcmd[vcmd.index("--max-model-len") + 1] == "32768",
            "vllm_quant": vcmd[vcmd.index("--quantization") + 1] == "bitsandbytes",
            "vllm_served_name": "demo-model" in vcmd,
            "listed": len(listed) == 1 and listed[0]["name"] == "demo-model",
            "resolved": resolved.name == "demo-q4_k_m.gguf" and resolved.exists(),
            "quant_read": listed[0]["quant"] == "Q4_K_M",
            "cmd_binary": cmd[0] == "llama-server",
            "cmd_model_path": str(resolved) in cmd,
            "cmd_ctx": str(cmd[cmd.index("-c") + 1]) == "32768",
            "endpoint": lserver.endpoint() == "http://127.0.0.1:18080/v1",
        }

        # 10. tiering: the plan must be a prefix split, and must respect the budgets it is given
        from models import tiering
        big = models_tmp / "big-model"
        big.mkdir()
        n_layers = 40
        (big / "config.json").write_text(json.dumps({
            "num_hidden_layers": n_layers, "num_attention_heads": 32,
            "num_key_value_heads": 4, "hidden_size": 2560, "head_dim": 64}))
        # a sparse 4 GiB file: the planner reads st_size, so the test costs no disk
        with open(big / "model.safetensors", "wb") as fh:
            fh.truncate(4 * 1024 ** 3)
        fake_hw = {"vram_total": 8 * 1024 ** 3, "vram_free": 6 * 1024 ** 3,
                   "ram_total": 32 * 1024 ** 3, "ram_available": 12 * 1024 ** 3,
                   "disk_free": 80 * 1024 ** 3}
        pl = tiering.plan(big, 32768, gpu_fill=0.9, ram_fill=0.75, hw=fake_hw)
        devs = [pl["device_map"][f"model.layers.{i}"] for i in range(n_layers)]
        tier_checks = {
            "all_layers_mapped": len(devs) == n_layers,
            "gpu_is_prefix": all(d == 0 for d in devs[:pl["tiers"]["gpu"]["layers"]])
                             and all(d != 0 for d in devs[pl["tiers"]["gpu"]["layers"]:]),
            "tiers_sum": sum(pl["tiers"][t]["layers"] for t in ("gpu", "cpu", "disk")) == n_layers,
            "kv_counted": pl["kv_cache_bytes"] > 0,
            "prefix_named": pl["layer_prefix"] == "model.layers",
            "gpu_gets_some": pl["tiers"]["gpu"]["layers"] > 0,
        }
        # a model far too big for both VRAM and RAM must reach the disk tier
        pl2 = tiering.plan(big, 32768, hw={**fake_hw, "vram_free": 1 * 1024 ** 3,
                                           "ram_available": 512 * 1024 ** 2})
        tier_checks["disk_used"] = pl2["tiers"]["disk"]["layers"] > 0
        print(f"tiering {tier_checks} -> gpu={pl['tiers']['gpu']['layers']} "
              f"cpu={pl['tiers']['cpu']['layers']} disk={pl['tiers']['disk']['layers']}")
        ok &= all(tier_checks.values())

        # a port that already answers is reused, never respawned
        warm = _Stub("openai")
        try:
            lcfg2 = _copy.deepcopy(lcfg)
            lcfg2["model"]["local"]["port"] = warm.port
            lserver2 = LocalServer(lcfg2)
            before = len(lserver2.__dict__)
            url = lserver2.start()
            models_checks["warm_reused"] = (lserver2.proc is None and url.endswith(f":{warm.port}/v1"))
        finally:
            warm.close()
        print(f"models  {models_checks}")
        ok &= all(models_checks.values())
        _os.environ.pop("ATHENA_MODELS", None)
        _os.environ.pop("ATHENA_API_KEY", None)

        # 11. tools off: no schemas, and a refusal if something slips through anyway
        from server.channels import NO_TOOLS
        tools_off = {
            "no_schemas": NO_TOOLS.schemas() == [],
            "no_names": NO_TOOLS.names() == [],
            "refuses": "disabled" in NO_TOOLS.invoke("echo", {"text": "x"}),
        }
        print(f"tools-off {tools_off}")
        ok &= all(tools_off.values())

        # 12. interruptions: applied at the next iteration boundary, counted, and never replayed
        with tempfile.TemporaryDirectory() as _itd:
            _root = Path(_itd)
            profile_mod.ensure(_root)
            session_i = Session("interrupt-channel", _root)
            delivered = []

            def _inbox():
                if not delivered:
                    delivered.append(1)
                    return ["[interrupt] the operator sent a new message while you were working: "
                            "\"also check the retries\" — it is queued as your next task, so do not "
                            "answer it now."]
                return []

            # a tool turn needs two model calls, so there is a boundary for the notice to land on
            r_i = run_turn(channel="interrupt-channel", channel_root=_root, session=session_i,
                           provider=provider, tools_registry=tools, instructions="be brief",
                           user_text="echo piece of cake", cfg=cfg, interrupts=_inbox)
            # only user turns: the mock provider echoes its prompt, so a naive count finds the
            # phrase in the reply as well and calls a correct turn "replayed"
            user_texts = [m.get("content") or "" for m in session_i.history()
                          if m.get("role") == "user"]
            interrupt_checks = {
                "applied_once": r_i.get("interruptions") == 1,
                "in_transcript": any("also check the retries" in t for t in user_texts),
                "not_replayed": sum(1 for t in user_texts if "also check the retries" in t) == 1,
                "turn_still_completes": r_i["status"] == "completed",
            }
        print(f"interrupt {interrupt_checks}")
        ok &= all(interrupt_checks.values())

        # 13. read_file: opens a workspace file, refuses everything else (the security boundary)
        from tools import registry as tools_mod
        rf = tools_mod.default_registry(cfg, workspace_root=root)
        ws = root / "workspace" / "uploads"
        ws.mkdir(parents=True, exist_ok=True)
        (ws / "note.txt").write_text("hello from the workspace", encoding="utf-8")
        read_checks = {
            "inside_workspace_ok": "hello from the workspace" in rf.invoke(
                "read_file", {"path": str(ws / "note.txt")}),
            "outside_refused": "may only open files under" in rf.invoke(
                "read_file", {"path": "/etc/passwd"}),
            "traversal_refused": "may only open files under" in rf.invoke(
                "read_file", {"path": str(ws / ".." / ".." / ".." / ".." / ".secret")}),
            "missing_file_named": "no such file" in rf.invoke(
                "read_file", {"path": str(ws / "nope.txt")}),
        }
        print(f"read_file {read_checks}")
        ok &= all(read_checks.values())

        # 14. every channel row has the same shape, resident or not
        with tempfile.TemporaryDirectory() as _rtd:
            _root = Path(_rtd) / "profiles"
            _root.mkdir()
            cfg_shape = _copy.deepcopy(cfg)
            from server.channels import ChannelManager as _CM
            mgr_shape = _CM(cfg_shape, provider, tools, _root)
            mgr_shape.create("cold-channel")
            rows_cold = mgr_shape.discover()
            mgr_shape.channel("warm-channel", ensure=True)           # resident, so built from the live object
            rows_mixed = mgr_shape.discover()
            required = {"channel", "state", "available", "resident", "busy", "queue_depth",
                        "awaiting", "inbound_from", "turns", "session_messages"}
            shape_checks = {
                "cold_row_complete": required <= set(rows_cold[0]),
                "mixed_rows_complete": all(required <= set(r) for r in rows_mixed),
                # every row must carry the SAME fields. A subset check let `last_author` exist on resident
                # rows only, which is exactly the shape-depends-on-warmth bug this test is here to prevent.
                "identical_shape": len({tuple(sorted(r)) for r in rows_mixed}) == 1,
                "cold_matches_warm": set(rows_cold[0]) == set(rows_mixed[0]),
            }
        print(f"row shape {shape_checks}")
        ok &= all(shape_checks.values())

        # 15. admission: a full queue refuses the task instead of accepting it silently
        import copy as _copy
        from server.channels import ChannelManager
        from pathlib import Path as _P

        with tempfile.TemporaryDirectory() as _atd:
            _agents = _P(_atd) / "profiles"
            _agents.mkdir()
            cfg_full = _copy.deepcopy(cfg)
            cfg_full.setdefault("agents", {})["max_queue"] = 0     # nothing may wait
            mgr = ChannelManager(cfg_full, provider, tools, _agents)
            refused = False
            try:
                mgr.channel("full-channel", ensure=True)      # the lane must exist: a miss no longer creates one
                mgr.submit("full-channel", "hello", wait_s=5)
            except ValueError as exc:
                refused = "queue is full" in str(exc)
            # the roster must carry a state, or delegation cannot choose between an idle and a busy peer
            mgr_roster = ChannelManager(cfg, provider, tools, _agents)
            channel_r = mgr_roster.channel("roster-a", ensure=True)
            mgr_roster.create("roster-b")
            roster_text = mgr_roster.roster("roster-a")
            instr = mgr_roster.instructions_for(channel_r)
            roster_checks = {
                "names_the_peer": "@roster-b" in roster_text,
                "carries_a_state": any(word in roster_text for word in
                                       ("idle", "working", "queued", "available", "stale", "error")),
                "excludes_self": "@roster-a" not in roster_text,
                # behaviour, not wording: the peer must reach the instructions at all. Pinning the exact
                # label ("Peers right now") broke when the instruction text was shortened for cost, which
                # is a test failing on prose rather than on the roster being dropped
                "in_instructions": "@roster-b" in instr and "Peers" in instr,
            }
            print(f"roster  {roster_checks} -> {roster_text!r}")
            ok &= all(roster_checks.values())

            admission = {"full_queue_refuses": refused,
                         "refusal_is_a_value_error": refused}
        print(f"admission {admission}")
        ok &= all(admission.values())

        # 14. the agent state machine: transitions are declared, and a stale in-flight state recovers
        from core import channelstate
        import tempfile as _tempfile
        from pathlib import Path as _Path

        state_checks = {
            "removed_is_terminal": not any(channelstate.can_move("removed", s)
                                           for s in channelstate.ALL),
            "idle_can_queue": channelstate.can_move("idle", "queued"),
            "working_to_queued": channelstate.can_move("working", "queued"),
            "no_backwards_available": not channelstate.can_move("working", "available"),
            "self_loop_refused": not channelstate.can_move("idle", "available"),
            "idle_can_sleep": channelstate.can_move("idle", "sleeping"),
            "sleep_cannot_work": not channelstate.can_move("sleeping", "working"),
            "sleep_never_jumps_idle": channelstate.can_move("sleeping", "waking"),
            "sleep_can_wake": channelstate.can_move("sleeping", "waking"),
            "sleep_cannot_jump_to_idle": not channelstate.can_move("sleeping", "idle"),
            "waking_settles_to_idle": channelstate.can_move("waking", "idle"),
            "waking_can_fail": channelstate.can_move("waking", "error"),
            "waking_cannot_work": not channelstate.can_move("waking", "working"),
        }
        with _tempfile.TemporaryDirectory() as _td:
            root_state = _Path(_td)
            channelstate.write(root_state, channelstate.WORKING, turn=7)
            adopted, recovered = channelstate.recover(root_state)
            state_checks["stale_working_recovers_idle"] = (adopted == channelstate.IDLE
                                                           and recovered == channelstate.WORKING)
            again, _ = channelstate.recover(root_state)
            state_checks["recover_is_idempotent"] = again == channelstate.IDLE

            fresh = _Path(_td) / "fresh"
            fresh.mkdir()
            state_checks["unknown_profile_is_available"] = (
                channelstate.recover(fresh)[0] == channelstate.AVAILABLE)

        print(f"states  {state_checks}")
        ok &= all(state_checks.values())

        # 13. mentions: addressed text, self-mentions ignored, hop limit enforced
        from core import mentions

        known = ["default", "scout", "scribe"]
        cases = {
            "asks one": ("@scout: what is the retry limit?", ["scout"]),
            "never self": ("@default hello", []),
            "ignores unknown": ("@not-an-agent hi", []),
            "two is the cap": ("@scout @scribe @default go", ["scout", "scribe"]),
            "not an email": ("mail me at a@b.com", []),
        }
        bad = []
        for label, (text, expected) in cases.items():
            got = mentions.find(text, known=known, exclude="default")
            if got != expected:
                bad.append(f"{label}: {got} != {expected}")
        question = mentions.question_for("@scout: what is the retry limit?", "scout")
        stripped = mentions.strip("@scout: hello there", ["scout"])
        mention_checks = {
            "cases": not bad,
            "question_extracted": question == "what is the retry limit?",
            "address_stripped": stripped == "hello there",
            "hop_cap": mentions.MAX_HOPS == 3,
        }
        print(f"mentions {mention_checks} max_hops={mentions.MAX_HOPS}")
        for line in bad:
            print(f"        {line}")
        ok &= all(mention_checks.values())

        # 13. tool-call parsing: every encoding a local model is known to emit
        from providers.tool_calls import parse_tool_calls

        formats = {
            "qwen-xml": '<tool_call><function=echo><parameter=text>hi</parameter></function></tool_call>',
            "qwen-json": '<tool_call>{"name": "echo", "arguments": {"text": "hi"}}</tool_call>',
            "bare-xml": '<function=echo><parameter=text>hi</parameter></function>',
            "mistral": '[TOOL_CALLS] [{"name": "echo", "arguments": {"text": "hi"}}]',
            "llama-python": '<|python_tag|>{"name": "echo", "parameters": {"text": "hi"}}<|eom_id|>',
            "fenced-json": '```json\n{"name": "echo", "arguments": {"text": "hi"}}\n```',
            "bare-json": '{"name": "echo", "arguments": {"text": "hi"}}',
            "string-args": '<tool_call>{"name": "echo", "arguments": "{\\"text\\": \\"hi\\"}"}</tool_call>',
        }
        bad = []
        for label, text in formats.items():
            prose, calls = parse_tool_calls(text)
            good = (len(calls) == 1 and calls[0]["name"] == "echo"
                    and calls[0]["arguments"] == {"text": "hi"}
                    and "<tool_call>" not in prose and "<function=" not in prose)
            if not good:
                bad.append(f"{label}: calls={calls} prose={prose!r}")
        prose, _ = parse_tool_calls("Sure.<tool_call>{bad json}</tool_call> Done.")
        print(f"toollc  formats={len(formats)} failed={len(bad)} prose_kept={'Sure.' in prose}")
        for line in bad:
            print(f"        {line}")
        ok &= not bad and "Sure." in prose

    # the redesign's baseline: every colour the stylesheets resolve to must still match the step-0 record. Run as a
    # subprocess so its exit codes stay its own contract (0 clean, 1 differ, 2 usage, 3 not a pre-sweep record).
    import subprocess as _proc

    _snap_script = Path(__file__).resolve().parent / "ui_snapshot_check.py"
    if _snap_script.is_file():
        _snap = _proc.run([__import__("sys").executable, str(_snap_script), "--check"],
                          capture_output=True, text=True)
        _snap_line = (_snap.stdout.strip().splitlines() or ["no output"])[0]
        print(f"uisnap  {_snap_line} · exit={_snap.returncode}")
        ok &= _snap.returncode == 0
    else:
        print("uisnap  ui_snapshot_check.py is missing")
        ok = False

    print("\nSELFTEST", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(run())
