#!/usr/bin/env python3
"""A resident voice: CosyVoice loaded once, cloning from a profile's voice bank.

    tools/cosyvoice-runtime/.venv311/bin/python athena-agent/scripts/tts_server.py \
        --voicebank profiles/default/voicebank --port 8092

POST /speak {"text": "…"}            → audio/wav bytes
POST /speak {"text": "…", "json":1}  → {"ok": true, "seconds": 0.9, "sample_rate": 22050, "bytes": 44100}
GET  /health                         → {"ok": true, "speaker": "…", "device": "cuda", "loaded_s": 1.2}

The reference clip and its transcript come from the voice bank (reference.wav / reference.txt), which is what a
zero-shot clone actually needs — not averaged weights.
"""

from __future__ import annotations

import argparse
import inspect
import io
import json
import os
import signal
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

_TREE = Path(__file__).resolve().parents[1]
if str(_TREE) not in sys.path:                      # `python scripts/tts_server.py` puts scripts/ on the path, not the tree
    sys.path.insert(0, str(_TREE))
from core import trace

LANE = "tts"
_state: dict = {}
_served = {"requests": 0, "chunks": 0, "bytes": 0, "failures": 0}


def _patch_hub() -> None:
    """CosyVoice imports `cached_download`, which huggingface_hub removed in 0.26.

    It is a rename — `hf_hub_download` is the same call under the current name — so the alias is restored rather than
    downgrading the library, which would drag transformers back with it.
    """
    import huggingface_hub

    if not hasattr(huggingface_hub, "cached_download"):
        huggingface_hub.cached_download = huggingface_hub.hf_hub_download


def load_xtts(reference: Path) -> dict:
    """XTTS-v2: cloning from a reference wav, no prompt text, no ONNX, no speaker registry."""
    import os

    import torch

    # Coqui asks for licence acceptance on stdin; a service has none, so the answer is given here
    os.environ.setdefault("COQUI_TOS_AGREED", "1")
    from TTS.api import TTS

    device = "cuda" if torch.cuda.is_available() else "cpu"
    started = time.time()
    with trace.span("tts.load", channel=LANE, engine="xtts", model="xtts_v2", device=device,
                    reference=reference.name) as sp:
        model = TTS("tts_models/multilingual/multi-dataset/xtts_v2").to(device)
        sp.ok(seconds=round(time.time() - started, 2))
    return {"engine": "xtts", "model": model, "device": device, "reference": str(reference),
            "sample_rate": 24000, "loaded_s": round(time.time() - started, 2),
            "cls": "TTS/xtts_v2", "kwargs": [], "prompt_text": ""}


def load_cosyvoice(model_dir: str, reference: Path) -> dict:
    """Load CosyVoice3, tolerating the constructor differences across 1/2/3."""
    _patch_hub()
    import torch
    from cosyvoice.cli import cosyvoice as cv

    cls = getattr(cv, "CosyVoice3", None) or getattr(cv, "CosyVoice2", None) or cv.CosyVoice
    accepted = set(inspect.signature(cls.__init__).parameters)
    kwargs = {}
    for name, value in (("load_jit", False), ("load_trt", False), ("load_vllm", False), ("fp16", False)):
        if name in accepted:
            kwargs[name] = value
    started = time.time()
    device = "cuda" if torch.cuda.is_available() else "cpu"
    with trace.span("tts.load", channel=LANE, engine="cosyvoice", model=Path(model_dir).name,
                    device=device, cls=cls.__name__) as sp:
        model = cls(model_dir, **kwargs)
        sp.ok(seconds=round(time.time() - started, 2), kwargs=sorted(kwargs))
    return {"model": model, "cls": cls.__name__, "kwargs": sorted(kwargs),
            "device": device,
            "loaded_s": round(time.time() - started, 2),
            "reference": str(reference), "sample_rate": int(getattr(model, "sample_rate", 22050))}


def clean_text(text: str) -> str:
    """Normalise what reaches the model.

    Typographic dashes, curly quotes and runs of punctuation are what the agent writes naturally and what the speaker
    trainer never saw; a single em dash in a long passage was enough to trip a CUDA assert, which then poisoned every
    later request in that process.
    """
    import re
    import unicodedata

    value = unicodedata.normalize("NFKC", str(text or ""))
    value = (value.replace("\u2014", "-").replace("\u2013", "-")
                  .replace("\u2018", "'").replace("\u2019", "'")
                  .replace("\u201c", '"').replace("\u201d", '"')
                  .replace("\u2026", "..."))
    value = re.sub(r"[*_`#>~|]+", " ", value)            # markdown: *emphasis*, `code`, # headings
    value = re.sub(r"([.!?]){2,}", r"\1", value)          # "....." becomes "."
    value = re.sub(r"\s{2,}", " ", value)
    value = re.sub(r"^[\W_]+$", "", value).strip()
    return value


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:                                        # noqa: N802
        if self.path.startswith("/health"):
            self._json(200, {"ok": True, "speaker": _state.get("speaker"), "cls": _state.get("cls"),
                             "kwargs": _state.get("kwargs"), "device": _state.get("device"),
                             "loaded_s": _state.get("loaded_s"), "sample_rate": _state.get("sample_rate")})
        else:
            self._json(404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:                                       # noqa: N802
        if not self.path.startswith("/speak"):
            trace.event("tts.refused", channel=LANE, reason="not found", path=self.path)
            self._json(404, {"ok": False, "error": "not found"})
            return
        length = int(self.headers.get("content-length") or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            trace.event("tts.refused", channel=LANE, reason="body is not JSON", bytes=length)
            self._json(400, {"ok": False, "error": "body must be JSON"})
            return
        text = clean_text(payload.get("text") or "")
        if not text:
            trace.event("tts.refused", channel=LANE, reason="empty text after clean_text",
                        raw_chars=len(str(payload.get("text") or "")))
            self._json(400, {"ok": False, "error": "text is required"})
            return
        with trace.span("tts.speak", channel=LANE, chars=len(text), words=len(text.split()),
                        engine=_state.get("engine"), json=bool(payload.get("json"))) as sp:
            self._speak(payload, text, sp, time.time())

    def _speak(self, payload: dict, text: str, sp, started: float) -> None:
        """Synthesise and answer. One span wraps it, so a failure exits with its frame and a poisoned CUDA
        context is one readable line before the process exits for a clean restart."""
        try:
            import numpy as np
            import soundfile as sf
            import torch

            model = _state["model"]
            if _state.get("engine") == "xtts":
                # Per-word (or per-group) synthesis, joined with an equal-power crossfade. Small groups keep latency flat
                # and let speech start almost at once, at the cost of prosody — each clip is synthesised without knowing
                # the next, so intonation resets. The crossfade stops the seams clicking; it cannot restore the melody.
                import numpy as _np

                size = int(payload.get("chunk_words") or _state.get("chunk_words") or 1)   # one word per clip by default
                size = max(1, min(size, 40))
                words = text.split()
                groups = [" ".join(words[i:i + size]) for i in range(0, len(words), size)] or [text]

                pieces = []
                last_error = ""
                for group in groups:
                    # A bare word with no punctuation can make XTTS fail; a full stop gives it a sentence to breathe.
                    samples = None
                    for attempt in (group, group.rstrip(".,!?;:") + "."):
                        try:
                            candidate = _np.asarray(
                                model.tts(text=attempt, speaker_wav=_state["reference"], language="en"),
                                dtype=_np.float32)
                        except Exception as exc:                  # noqa: BLE001
                            last_error = f"{type(exc).__name__}: {exc}"[:200]
                            continue
                        if candidate.size:
                            samples = candidate
                            break
                    if samples is None:
                        trace.event("tts.chunk.failed", level="debug", channel=LANE, chunk=group[:40],
                                    error=last_error)
                        continue
                    _served["chunks"] += 1
                    if samples.size:
                        # each clip arrives with its own lead and tail of silence; concatenating them stretches a
                        # sentence to several times its length, so the edges are trimmed to the speech envelope
                        peak = float(_np.max(_np.abs(samples)) or 0.0)
                        if peak > 0.01:
                            threshold = peak * 0.06
                            loud = _np.flatnonzero(_np.abs(samples) > threshold)
                            if loud.size:
                                pad = int(int(_state["sample_rate"]) * 0.02)
                                lo = max(0, int(loud[0]) - pad)
                                hi = min(len(samples), int(loud[-1]) + pad)
                                samples = samples[lo:hi]
                        pieces.append(samples)

                if not pieces:
                    # last resort: the whole passage in one call, which is what worked before chunking existed
                    trace.event("tts.speak.fallback", level="debug", channel=LANE, groups=len(groups),
                                error=last_error)
                    try:
                        whole = _np.asarray(model.tts(text=text, speaker_wav=_state["reference"], language="en"),
                                            dtype=_np.float32)
                        if whole.size:
                            pieces = [whole]
                    except Exception as exc:                      # noqa: BLE001
                        last_error = f"{type(exc).__name__}: {exc}"[:200]
                if not pieces:
                    sp.failed(RuntimeError(f"no chunk could be synthesised ({last_error})"))
                    self._json(500, {"ok": False, "error": f"no chunk could be synthesised ({last_error})"})
                    return

                fade_ms = float(payload.get("crossfade_ms") or _state.get("crossfade_ms") or 15.0)
                fade = int(int(_state["sample_rate"]) * fade_ms / 1000.0)
                audio_out = pieces[0]
                for nxt in pieces[1:]:
                    f = min(fade, len(audio_out), len(nxt))
                    if f > 0:
                        ramp = _np.linspace(0.0, 1.0, f, dtype=_np.float32)
                        # only the last `f` samples are crossfaded; the rest of the clip passes through untouched
                        faded = audio_out[-f:] * (1.0 - ramp) + nxt[:f] * ramp
                        audio_out = _np.concatenate([audio_out[:-f], faded, nxt[f:]])
                    else:
                        audio_out = _np.concatenate([audio_out, nxt])
                arr = audio_out
                rate = int(_state["sample_rate"])
                if payload.get("json"):
                    self._json(200, {"ok": True, "seconds": round(time.time() - started, 3),
                                     "sample_rate": rate, "samples": int(arr.size), "engine": "xtts"})
                    _served["requests"] += 1
                    sp.ok(engine="xtts", samples=int(arr.size), sample_rate=rate, chunks=len(pieces),
                          seconds=round(time.time() - started, 3))
                    return
                buffer = io.BytesIO()
                sf.write(buffer, arr, rate, format="WAV", subtype="PCM_16")
                body = buffer.getvalue()
                self.send_response(200)
                self.send_header("content-type", "audio/wav")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                _served["requests"] += 1
                _served["bytes"] += len(body)
                sp.ok(engine="xtts", bytes=len(body), sample_rate=rate, chunks=len(pieces),
                      seconds=round(time.time() - started, 3))
                return

            # A registered speaker (built from every clip in the bank) needs no prompt per call. Falling back to the
            # reference clip only happens before the bank exists.
            speaker_id = _state.get("speaker_id") or ""
            available = []
            try:
                available = model.list_available_spks()
            except Exception:
                available = []
            use_bank = bool(speaker_id and speaker_id in available)
            prompt = "" if use_bank else str(_state["reference"])
            prompt_text = _state["prompt_text"]
            if use_bank:
                # the prompt text still has to be supplied even when the speaker comes from the bank:
                # an empty string tokenises to nothing and the flow matching then gets a 3-frame input
                pieces = [item["tts_speech"] for item in
                          model.inference_zero_shot(text, prompt_text, None, zero_shot_spk_id=speaker_id,
                                                    stream=False)]
            else:
                pieces = [item["tts_speech"] for item in
                          model.inference_zero_shot(text, prompt_text, prompt, stream=False)]
            audio = torch.cat(pieces, dim=1) if len(pieces) > 1 else pieces[0]
            rate = _state["sample_rate"]
            if payload.get("json"):
                self._json(200, {"ok": True, "seconds": round(time.time() - started, 3), "sample_rate": rate,
                                 "samples": int(audio.shape[1])})
                _served["requests"] += 1
                sp.ok(samples=int(audio.shape[1]), sample_rate=rate, speaker="bank" if use_bank else "reference",
                      seconds=round(time.time() - started, 3))
                return
            buffer = io.BytesIO()
            sf.write(buffer, audio.squeeze(0).detach().cpu().numpy(), rate, format="WAV", subtype="PCM_16")
            body = buffer.getvalue()
            self.send_response(200)
            self.send_header("content-type", "audio/wav")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            _served["requests"] += 1
            _served["bytes"] += len(body)
            sp.ok(bytes=len(body), sample_rate=rate, speaker="bank" if use_bank else "reference",
                  seconds=round(time.time() - started, 3))
        except Exception as exc:                                     # noqa: BLE001
            _served["failures"] += 1
            sp.failed(exc)                                           # the ✗ line carries the frame
            message = f"{type(exc).__name__}: {exc}"
            # a CUDA assert leaves the context unusable: exit so the supervisor starts a clean worker instead of
            # answering every later request with the same poisoned error
            if "device-side assert" in message or "CUDA error" in message:
                # one readable line, then die: the HTTP 500 above is all a caller can see, and it says nothing
                trace.event("tts.worker.fatal", channel=LANE, action="exit", code=3,
                            why="cuda context is unusable after a device-side assert",
                            requests=_served["requests"], chunks=_served["chunks"], failures=_served["failures"])
                sys.stderr.flush()
                self._json(500, {"ok": False, "error": "tts worker is restarting (cuda context poisoned)"})
                os._exit(3)
            self._json(500, {"ok": False, "error": message})

    def log_message(self, fmt: str, *args) -> None:
        return


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8092)
    ap.add_argument("--model", default="")
    ap.add_argument("--voicebank", default="")
    args = ap.parse_args()

    root = Path(__file__).resolve().parents[2]
    model_dir = args.model or str(root / "models" / "FunAudioLLM" / "Fun-CosyVoice3-0.5B-2512")
    bank = Path(args.voicebank or (root / "profiles" / "default" / "voicebank"))
    reference = bank / "reference.wav"
    trace.event("tts.start", channel=LANE, pid=os.getpid(), port=args.port, host="127.0.0.1",
                bank=bank.name, model=Path(model_dir).name)
    prompt_text = (bank / "reference.txt").read_text(encoding="utf-8").strip() if (bank / "reference.txt").exists() else ""
    if not reference.exists():
        trace.event("tts.refused", channel=LANE, reason="no reference clip", bank=str(bank))
        print(json.dumps({"ok": False, "error": f"no reference clip in {bank}"}), flush=True)
        return 2

    try:
        _state.update(load_xtts(reference))
    except Exception as exc:                                    # noqa: BLE001
        # the fallback is a real decision, and the reason the first engine failed is the thing worth keeping
        trace.failure("tts.load.failed", exc, channel=LANE, engine="xtts", fallback="cosyvoice")
        print(json.dumps({"xtts_failed": f"{type(exc).__name__}: {exc}", "falling_back": "cosyvoice"}), flush=True)
        _state.update(load_cosyvoice(model_dir, reference))
    _state["prompt_text"] = prompt_text[:400]
    _state["speaker_id"] = _state.get("speaker") or ""
    _state["speaker"] = json.loads((bank / "speaker.json").read_text()).get("speaker") if (bank / "speaker.json").exists() else bank.parent.name
    print(json.dumps({"listening": f"http://127.0.0.1:{args.port}", "speaker": _state["speaker"],
                      "cls": _state["cls"], "kwargs": _state["kwargs"], "device": _state["device"],
                      "loaded_s": _state["loaded_s"]}), flush=True)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    trace.event("tts.listening", channel=LANE, port=args.port, speaker=_state["speaker"], cls=_state["cls"],
                device=_state["device"], loaded_s=_state["loaded_s"], sample_rate=_state.get("sample_rate"))

    def _on_term(signum, _frame):
        trace.event("tts.stop", channel=LANE, reason="signal", signal=signum, requests=_served["requests"],
                    bytes=_served["bytes"], failures=_served["failures"])
        raise SystemExit(0)

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _on_term)
        except (ValueError, OSError):                           # not the main thread: the default handler stands
            pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        trace.event("tts.stop", channel=LANE, reason="interrupted", requests=_served["requests"],
                    bytes=_served["bytes"], failures=_served["failures"])
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
