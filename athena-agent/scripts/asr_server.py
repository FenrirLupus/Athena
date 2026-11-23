#!/usr/bin/env python3
"""A resident transcriber: whisper loaded once, so each utterance costs inference only.

Run from the voice runtime:

    tools/voice-runtime/.venv/bin/python athena-agent/scripts/asr_server.py --port 8091

POST /transcribe with a wav (or any ffmpeg-readable) body → {"ok": true, "text": "…", "seconds": 0.8}
GET  /health → {"ok": true, "model": "…", "device": "cuda"}

Why a server and not a subprocess per request: loading whisper-small takes ~8 seconds. Paying that per utterance makes a
call unusable, so the weights live in memory for the life of the process and the request path is decode + infer.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import tempfile
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

_TREE = Path(__file__).resolve().parents[1]
if str(_TREE) not in sys.path:                      # `python scripts/asr_server.py` puts scripts/ on the path, not the tree
    sys.path.insert(0, str(_TREE))
from core import trace

LANE = "asr"
MODEL = Path(__file__).resolve().parents[2] / "models" / "openai" / "whisper-small"
_state: dict = {}
_served = {"requests": 0, "bytes": 0, "failures": 0}


def _fatal(exc: BaseException) -> bool:
    """A CUDA assert poisons the context: every later request would answer the same 500, so the process exits and the
    supervisor starts a clean worker. Matched here so the decision is one line, not a guess at the call site."""
    message = f"{type(exc).__name__}: {exc}"
    return "device-side assert" in message or "CUDA error" in message


def load(model_dir: Path) -> dict:
    import torch
    from transformers import pipeline
    device = 0 if torch.cuda.is_available() else -1
    started = time.time()
    with trace.span("asr.load", channel=LANE, model=model_dir.name,
                    device="cuda" if device == 0 else "cpu") as sp:
        asr = pipeline("automatic-speech-recognition", model=str(model_dir), device=device,
                       chunk_length_s=30, return_timestamps=False)
        sp.ok(seconds=round(time.time() - started, 2))
    return {"asr": asr, "device": "cuda" if device == 0 else "cpu",
            "loaded_s": round(time.time() - started, 2), "model": str(model_dir)}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, code: int, payload: dict) -> int:
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        return len(body)

    def do_GET(self) -> None:                                   # noqa: N802
        if self.path.startswith("/health"):
            self._send(200, {"ok": True, "model": _state.get("model"), "device": _state.get("device"),
                             "loaded_s": _state.get("loaded_s")})
        else:
            self._send(404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:                                  # noqa: N802
        if not self.path.startswith("/transcribe"):
            trace.event("asr.refused", channel=LANE, reason="not found", path=self.path)
            self._send(404, {"ok": False, "error": "not found"})
            return
        length = int(self.headers.get("content-length") or 0)
        if length <= 0:
            trace.event("asr.refused", channel=LANE, reason="empty body")
            self._send(400, {"ok": False, "error": "empty body"})
            return
        with trace.span("asr.request", channel=LANE, bytes=length) as sp:
            raw = self.rfile.read(length)
            started = time.time()
            try:
                with tempfile.TemporaryDirectory(prefix="asr-") as work:
                    src = Path(work) / "in.wav"
                    src.write_bytes(raw)
                    out = _state["asr"](str(src))
            except Exception as exc:                            # noqa: BLE001
                _served["failures"] += 1
                sp.failed(exc)                                  # the ✗ line carries the raising frame
                if _fatal(exc):
                    # one readable line, then exit: a poisoned CUDA context answers every later request the same way
                    trace.event("asr.worker.fatal", channel=LANE, action="exit", code=3,
                                why="cuda context is unusable after a device-side assert",
                                requests=_served["requests"], failures=_served["failures"])
                    sys.stderr.flush()
                    self._send(500, {"ok": False, "error": "asr worker is restarting (cuda context poisoned)"})
                    os._exit(3)
                self._send(500, {"ok": False, "error": f"{type(exc).__name__}: {exc}"})
                return
            text = (out.get("text") or "").strip()
            sent = self._send(200, {"ok": True, "text": text, "seconds": round(time.time() - started, 3),
                                    "device": _state.get("device")})
            _served["requests"] += 1
            _served["bytes"] += length
            sp.ok(chars=len(text), bytes_out=sent, infer_s=round(time.time() - started, 3))

    def log_message(self, fmt: str, *args) -> None:              # quiet: the ledger is elsewhere
        return


def _stop(reason: str, code: int = 0) -> None:
    trace.event("asr.stop", channel=LANE, reason=reason, code=code,
                requests=_served["requests"], failures=_served["failures"], bytes=_served["bytes"])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8091)
    ap.add_argument("--model", default=str(MODEL))
    args = ap.parse_args()

    def _on_term(signum, _frame):
        _stop("signal", 0)
        raise SystemExit(0)

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _on_term)
        except (ValueError, OSError):                           # not the main thread: the default handler stands
            pass
    trace.event("asr.start", channel=LANE, pid=os.getpid(), port=args.port, host="127.0.0.1",
                model=Path(args.model).name)
    _state.update(load(Path(args.model)))
    print(json.dumps({"listening": f"http://127.0.0.1:{args.port}", **_state_no_asr()}), flush=True)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    trace.event("asr.listening", channel=LANE, port=args.port, device=_state.get("device"),
                loaded_s=_state.get("loaded_s"))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        _stop("interrupted")
    finally:
        server.server_close()
    return 0


def _state_no_asr() -> dict:
    return {k: v for k, v in _state.items() if k != "asr"}


if __name__ == "__main__":
    raise SystemExit(main())
