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
import tempfile
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

MODEL = Path(__file__).resolve().parents[2] / "models" / "openai" / "whisper-small"
_state: dict = {}


def load(model_dir: Path) -> dict:
    import torch
    from transformers import pipeline
    device = 0 if torch.cuda.is_available() else -1
    started = time.time()
    asr = pipeline("automatic-speech-recognition", model=str(model_dir), device=device,
                   chunk_length_s=30, return_timestamps=False)
    return {"asr": asr, "device": "cuda" if device == 0 else "cpu",
            "loaded_s": round(time.time() - started, 2), "model": str(model_dir)}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:                                   # noqa: N802
        if self.path.startswith("/health"):
            self._send(200, {"ok": True, "model": _state.get("model"), "device": _state.get("device"),
                             "loaded_s": _state.get("loaded_s")})
        else:
            self._send(404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:                                  # noqa: N802
        if not self.path.startswith("/transcribe"):
            self._send(404, {"ok": False, "error": "not found"})
            return
        length = int(self.headers.get("content-length") or 0)
        if length <= 0:
            self._send(400, {"ok": False, "error": "empty body"})
            return
        raw = self.rfile.read(length)
        started = time.time()
        try:
            with tempfile.TemporaryDirectory(prefix="asr-") as work:
                src = Path(work) / "in.wav"
                src.write_bytes(raw)
                out = _state["asr"](str(src))
            text = (out.get("text") or "").strip()
            self._send(200, {"ok": True, "text": text, "seconds": round(time.time() - started, 3),
                             "device": _state.get("device")})
        except Exception as exc:                                # noqa: BLE001
            self._send(500, {"ok": False, "error": f"{type(exc).__name__}: {exc}"})

    def log_message(self, fmt: str, *args) -> None:              # quiet: the ledger is elsewhere
        return


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8091)
    ap.add_argument("--model", default=str(MODEL))
    args = ap.parse_args()
    _state.update(load(Path(args.model)))
    print(json.dumps({"listening": f"http://127.0.0.1:{args.port}", **_state_no_asr()}), flush=True)
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()
    return 0


def _state_no_asr() -> dict:
    return {k: v for k, v in _state.items() if k != "asr"}


if __name__ == "__main__":
    raise SystemExit(main())
