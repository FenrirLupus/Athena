"""End-to-end: Qwen3.5-4B from Hugging Face, tiered across GPU/CPU/disk, answering through the loop."""
import sys, json, tempfile, copy, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import loader
from providers import base as pbase
from core import profile as P
from core.loop import run_turn
from core.session import Session
from models import tiering
from tools import registry as treg

cfg = copy.deepcopy(loader.load())
cfg["model"].update({"provider": "openai", "window": 32768, "max_output_tokens": 120,
                     "temperature": 0.3})
cfg["model"]["local"] = {
    "enabled": True, "backend": "transformers", "name": "qwen3.5-4b",
    "ctx": 32768, "port": 8099, "startup_timeout_s": 1200,
    "python": str(Path(__file__).resolve().parents[1].parent / "model-runtime" / ".venv" / "bin" / "python"),
    "gpu_memory_utilization": 0.90, "ram_fill": 0.75,
}

provider = pbase.build(cfg)
print("provider:", json.dumps(provider.describe()), flush=True)

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    P.ensure(root)
    (root / "assistant" / "ASSISTANT.md").write_text(
        "---\nname_first: Athena\n---\n\nI am Athena, a persistent local agent.\n", encoding="utf-8")
    (root / "user" / "USER.md").write_text(
        "---\nname_nick: Fen\n---\n\nPrefers short, direct answers.\n", encoding="utf-8")
    session = Session("local-test", root)
    tools = treg.default_registry(cfg)

    started = time.time()
    r = run_turn(channel="local-test", channel_root=root, session=session, provider=provider,
                 tools_registry=tools,
                 instructions="Answer in one short sentence.", user_text="hello",
                 cfg=cfg)
    print("elapsed:", round(time.time() - started, 1), "s")
    print("status:", r["status"], "| calls:", r["calls"], "| seconds:", r["seconds"])
    print("reply:", r["reply"][:400])
    print("server:", provider.server.base_url, "| tools_ok:", provider.tools_ok)

    # a second turn proves the server is warm, not restarted
    r2 = run_turn(channel="local-test", channel_root=root, session=session, provider=provider,
                  tools_registry=tools, instructions="Answer in one short sentence.",
                  user_text="what is your name?", cfg=cfg)
    print("turn2:", round(r2["seconds"], 1), "s |", r2["status"], "|", r2["reply"][:200])
