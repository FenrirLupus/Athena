#!/usr/bin/env python3
"""Speech in and out for Athena, run from the voice runtime venv.

    tools/voice-runtime/.venv/bin/python scripts/voice.py transcribe <audio> [--model <dir>]
    tools/voice-runtime/.venv/bin/python scripts/voice.py speak "<text>" --reference <wav> --out <wav>
    tools/voice-runtime/.venv/bin/python scripts/voice.py doctor

Every command prints one JSON object on stdout and exits non-zero on failure, so the caller (the server, a test, a
person at a terminal) parses one shape rather than three. Nothing here downloads: the models are on disk and are
addressed by path.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

TREE = Path(__file__).resolve().parents[1]          # athena-agent/, which holds core/
ROOT = Path(__file__).resolve().parents[2]          # <root>/.athena
if str(TREE) not in sys.path:                       # `python scripts/voice.py` puts scripts/ on the path, not the tree
    sys.path.insert(0, str(TREE))
from core import trace

LANE = "voice"
DEFAULT_WHISPER = ROOT / "models" / "openai" / "whisper-small"
DEFAULT_COSY = ROOT / "models" / "FunAudioLLM" / "Fun-CosyVoice3-0.5B-2512"


def emit(payload: dict, code: int = 0) -> int:
    print(json.dumps(payload, ensure_ascii=False))
    return code


def doctor() -> int:
    with trace.span("voice.doctor", channel=LANE) as sp:
        report = {"python": sys.version.split()[0]}
        try:
            import torch
            report["torch"] = torch.__version__
            report["cuda"] = torch.cuda.is_available()
            report["device"] = torch.cuda.get_device_name(0) if torch.cuda.is_available() else "cpu"
        except Exception as exc:                                # noqa: BLE001
            report["torch"] = f"missing: {exc}"
        for name, path in (("whisper", DEFAULT_WHISPER), ("cosyvoice", DEFAULT_COSY)):
            report[name] = {"path": str(path), "present": path.exists()}
        try:
            import transformers
            report["transformers"] = transformers.__version__
        except Exception as exc:                                # noqa: BLE001
            report["transformers"] = f"missing: {exc}"
        try:
            import cosyvoice                                    # noqa: F401
            report["cosyvoice_package"] = True
        except Exception:
            report["cosyvoice_package"] = False
        sp.ok(device=report.get("device", "?"), cuda=report.get("cuda"),
              whisper=report["whisper"]["present"], cosyvoice=report["cosyvoice"]["present"],
              cosyvoice_package=report["cosyvoice_package"])
    return emit(report)


def transcribe(audio: str, model_dir: str) -> int:
    started = time.time()
    source = Path(audio)
    try:
        import torch
        from transformers import pipeline
    except Exception as exc:                                    # noqa: BLE001
        trace.failure("voice.transcribe.failed", exc, channel=LANE, audio=source.name,
                      reason="transformers/torch not installed")
        return emit({"ok": False, "error": f"transformers/torch not installed: {exc}"}, 2)

    with trace.span("voice.transcribe", channel=LANE, audio=source.name,
                    bytes=source.stat().st_size if source.is_file() else 0,
                    model=Path(model_dir).name) as sp:
        device = 0 if torch.cuda.is_available() else -1
        asr = pipeline("automatic-speech-recognition", model=model_dir,
                       device=device, chunk_length_s=30, return_timestamps=False)
        result = asr(audio)
        text = (result.get("text") or "").strip()
        sp.ok(chars=len(text), device="cuda" if device == 0 else "cpu",
              seconds=round(time.time() - started, 3))
    return emit({"ok": True, "text": text, "seconds": round(time.time() - started, 3),
                 "device": "cuda" if device == 0 else "cpu", "model": model_dir})


def speak(text: str, reference: str, out: str, model_dir: str) -> int:
    """CosyVoice zero-shot: a reference clip plus its transcript, not averaged weights."""
    try:
        from cosyvoice.cli.cosyvoice import CosyVoice      # type: ignore
        from cosyvoice.utils.file_utils import load_wav     # type: ignore
    except Exception as exc:                                    # noqa: BLE001
        trace.failure("voice.speak.failed", exc, channel=LANE, chars=len(text), out=Path(out).name,
                      reason="cosyvoice is not importable")
        return emit({"ok": False, "error": f"cosyvoice is not importable: {exc}",
                     "hint": "the runtime needs the FunAudioLLM/CosyVoice package, not only the model directory"}, 2)

    import torchaudio

    reference_txt = Path(reference).with_suffix(".txt")
    prompt_text = reference_txt.read_text(encoding="utf-8").strip() if reference_txt.exists() else ""
    started = time.time()
    with trace.span("voice.speak", channel=LANE, chars=len(text), out=Path(out).name,
                    reference=Path(reference).name, model=Path(model_dir).name) as sp:
        model = CosyVoice(model_dir, load_jit=False, load_trt=False)
        prompt = load_wav(reference, 16000)
        pieces = []
        for item in model.inference_zero_shot(text, prompt_text, prompt, stream=False):
            pieces.append(item["tts_speech"])
        audio = torchaudio.functional.resample(pieces[0], 22050, 16000) if len(pieces) == 1 else pieces[0]
        torchaudio.save(out, audio, 16000)
        written = Path(out)
        sp.ok(bytes=written.stat().st_size if written.is_file() else 0, pieces=len(pieces),
              prompt_chars=len(prompt_text), seconds=round(time.time() - started, 3))
    return emit({"ok": True, "out": out, "seconds": round(time.time() - started, 3),
                 "reference": reference, "prompt_text_chars": len(prompt_text)})


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    t = sub.add_parser("transcribe")
    t.add_argument("audio")
    t.add_argument("--model", default=str(DEFAULT_WHISPER))

    s = sub.add_parser("speak")
    s.add_argument("text")
    s.add_argument("--reference", required=True)
    s.add_argument("--out", required=True)
    s.add_argument("--model", default=str(DEFAULT_COSY))

    sub.add_parser("doctor")
    args = ap.parse_args()
    trace.event("voice.run", channel=LANE, cmd=args.cmd)

    if args.cmd == "doctor":
        return doctor()
    if args.cmd == "transcribe":
        return transcribe(args.audio, args.model)
    return speak(args.text, args.reference, args.out, args.model)


if __name__ == "__main__":
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    sys.exit(main())
