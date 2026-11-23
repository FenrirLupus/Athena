#!/usr/bin/env python3
"""Build a reloadable speaker from every clip in a profile's voice bank.

CosyVoice keeps speakers in `spk2info` (saved as `spk2info.pt` beside the model). `add_zero_shot_spk` registers one
speaker from one clip, so a multi-clip speaker is built by registering each clip, averaging the embeddings, and writing
one consolidated entry — then deleting the per-clip temporaries.

    tools/cosyvoice-runtime/.venv311/bin/python athena-agent/scripts/voicebank_speaker.py \
        --voicebank profiles/default/voicebank --speaker kali --model models/FunAudioLLM/Fun-CosyVoice3-0.5B-2512

The result is what a call uses: `inference_zero_shot(text, "", None, zero_shot_spk_id="kali")` — no prompt clip, no
per-call embedding work.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TREE = Path(__file__).resolve().parents[1]
if str(TREE) not in sys.path:                       # `python scripts/voicebank_speaker.py` puts scripts/ on the path
    sys.path.insert(0, str(TREE))
from core import trace

LANE = "voicebank"


def _say(payload: dict) -> None:
    """One JSON object per line on stdout: this script's output, read by whoever built the bank. Not a diagnostic."""
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--voicebank", required=True)
    ap.add_argument("--speaker", required=True)
    ap.add_argument("--model", default=str(ROOT / "models" / "FunAudioLLM" / "Fun-CosyVoice3-0.5B-2512"))
    ap.add_argument("--limit", type=int, default=0, help="use at most N clips (0 = all)")
    args = ap.parse_args()

    import huggingface_hub
    import torch

    # CosyVoice imports `cached_download`, removed from huggingface_hub in 0.26 (same shim as the TTS worker)
    if not hasattr(huggingface_hub, "cached_download"):
        huggingface_hub.cached_download = huggingface_hub.hf_hub_download

    from cosyvoice.cli.cosyvoice import CosyVoice3

    bank = Path(args.voicebank)
    audio_dir = bank / "dataset" / "audio"
    meta_path = bank / "dataset" / "metadata.jsonl"
    if not audio_dir.is_dir():
        trace.event("voicebank.refused", level="error", channel=LANE, reason="no dataset/audio", bank=str(bank))
        _say({"ok": False, "error": f"no dataset/audio in {bank}"})
        return 2

    transcripts = {}
    if meta_path.exists():
        for line in meta_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            transcripts[Path(row["file_name"]).name] = (row.get("transcript") or "").strip()

    clips = sorted(audio_dir.glob("*.wav"))
    if args.limit:
        clips = clips[: args.limit]
    if not clips:
        trace.event("voicebank.refused", level="error", channel=LANE, reason="no clips", bank=str(bank))
        _say({"ok": False, "error": "no clips"})
        return 2

    _say({"loading": args.model})
    with trace.span("voicebank.speaker.load", channel=LANE, model=Path(args.model).name, speaker=args.speaker) as sp:
        started = time.time()
        model = CosyVoice3(args.model, load_trt=False, load_vllm=False, fp16=False)
        sp.ok(seconds=round(time.time() - started, 2))
    frontend = model.frontend

    import subprocess as sp_run
    import tempfile

    registered = []
    skipped = []
    work = tempfile.mkdtemp(prefix="voicebank-")
    for index, clip in enumerate(clips, start=1):
        text = transcripts.get(clip.name) or (bank / "reference.txt").read_text(encoding="utf-8").strip()
        temp_id = f"{args.speaker}-tmp-{index:02d}"
        # the speech tokeniser refuses anything over 30 s, so each clip is cut to its first 30 s
        cut = Path(work) / f"clip-{index:02d}.wav"
        try:
            sp_run.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(clip),
                        "-t", "30", "-ar", "16000", "-ac", "1", str(cut)], check=True, timeout=120)
        except Exception as exc:                                  # noqa: BLE001
            # not fatal: the original clip is tried instead, which is a real fallback worth one line
            trace.event("voicebank.speaker.cut.failed", level="debug", channel=LANE, clip=clip.name,
                        fallback="original clip", error=f"{type(exc).__name__}: {exc}"[:160])
            cut = clip
        try:
            model.add_zero_shot_spk(text, str(cut), temp_id)
            registered.append(temp_id)
            trace.event("voicebank.speaker.clip", level="debug", channel=LANE, index=index, of=len(clips),
                        clip=clip.name, chars=len(text))
        except Exception as exc:                                  # noqa: BLE001
            skipped.append(clip.name)
            trace.event("voicebank.speaker.skipped", level="info", channel=LANE, index=index, clip=clip.name,
                        error=f"{type(exc).__name__}: {exc}"[:160])
            _say({"clip": clip.name, "skipped": f"{type(exc).__name__}: {exc}"})

    if not registered:
        trace.event("voicebank.refused", level="error", channel=LANE, reason="no clip could be registered",
                    clips=len(clips), skipped=len(skipped))
        _say({"ok": False, "error": "no clip could be registered"})
        return 3

    info = frontend.spk2info
    entries = [info[i] for i in registered if i in info]
    first = entries[0]
    averaged: dict = {}
    for key, value in first.items():
        if isinstance(value, torch.Tensor) and value.is_floating_point():
            stack = [e[key] for e in entries if key in e and isinstance(e[key], torch.Tensor)
                     and e[key].is_floating_point() and e[key].shape == value.shape]
            if stack:
                averaged[key] = torch.stack(stack).mean(dim=0)
        elif isinstance(value, str):
            averaged[key] = value

    # keep one representative's remaining fields (feature/prompt text) so the entry is complete
    entry = dict(first)
    entry.update(averaged)
    info[args.speaker] = entry
    for temp_id in registered:
        info.pop(temp_id, None)

    model.save_spkinfo()
    counts = {"clips": len(clips), "registered": len(registered), "averaged_tensors": len(averaged),
              "speaker": args.speaker, "spk2info": str(Path(args.model) / "spk2info.pt"),
              "available": model.list_available_spks()}
    (bank / "speaker_bank.json").write_text(json.dumps(counts, indent=2) + "\n", encoding="utf-8")
    trace.event("voicebank.speaker.done", channel=LANE, speaker=args.speaker, clips=len(clips),
                registered=len(registered), skipped=len(skipped), averaged_tensors=len(averaged),
                available=len(counts["available"]), spk2info=counts["spk2info"])
    _say({"ok": True, **counts})
    return 0


if __name__ == "__main__":
    sys.exit(main())
