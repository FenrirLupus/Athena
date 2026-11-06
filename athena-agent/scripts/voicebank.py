#!/usr/bin/env python3
"""Build a voice bank from a profile's recordings.

A voice bank is three things, and only the third is optional:

  1. reference.wav + reference.txt   the zero-shot prompt a cloning model actually takes (CosyVoice, XTTS)
  2. dataset/                        every clip at 16 kHz mono, with an HF-datasets-shaped metadata.jsonl
  3. speaker.json                    statistics over the set, and the averaged embedding when a torch install exists

On averaging: a zero-shot cloning model does not take averaged weights. CosyVoice reads a *prompt* — a few seconds of
clean speech plus its transcript — and derives the speaker from it at synthesis time. So the artefact that matters is a
good reference clip, not a blend of weights; the average is kept as a voiceprint for comparison and future fine-tuning,
and it is computed only when a speaker encoder is actually installed.

Usage:
    python3 scripts/voicebank.py --profile default
    python3 scripts/voicebank.py --profile default --reference-seconds 20
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]           # <root>/.athena
PROFILES = ROOT / "profiles"
AUDIO_SUFFIXES = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".opus"}


def run(cmd: list[str]) -> str:
    return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout


def have(tool: str) -> bool:
    return shutil.which(tool) is not None


def probe(path: Path) -> dict:
    """Duration and loudness, both from ffprobe/ffmpeg — no python audio libraries needed."""
    out = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
               "-of", "default=nw=1:nk=1", str(path)]).strip()
    try:
        duration = float(out)
    except ValueError:
        duration = 0.0
    loud = 0.0
    try:
        vol = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(path), "-af", "volumedetect",
                              "-f", "null", "-"], capture_output=True, text=True).stderr
        match = re.search(r"mean_volume:\s*(-?\d+(?:\.\d+)?) dB", vol)
        if match:
            loud = float(match.group(1))
    except Exception:
        pass
    return {"seconds": round(duration, 3), "mean_db": loud}


def to_wav(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
         "-ar", "16000", "-ac", "1", "-sample_fmt", "s16", str(dst)])


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def transcript_hint(name: str, speaker: str) -> str:
    """The clip name carries its spoken text after the timestamp: `Kali-…-<date>-<time>-<text>.mp3`."""
    stem = Path(name).stem
    stem = re.sub(r"^" + re.escape(speaker) + r"-", "", stem, flags=re.I)
    stem = re.sub(r"^\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-", "", stem)
    return stem.replace("-", " ").replace("_", " ").strip()


def maybe_embedding(wavs: list[Path]) -> dict | None:
    """Average a speaker embedding, when an encoder is installed.

    Deliberately optional: the app venv has no torch, and a bank that pretends to have an embedding is worse than one
    that says it does not.
    """
    try:
        import numpy as np  # noqa: F401
        import torch
        from transformers import AutoFeatureExtractor, AutoModel  # noqa: F401
    except Exception:
        return None
    return {"note": "torch present but no speaker encoder is configured; averaging skipped"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="default")
    ap.add_argument("--reference-seconds", type=int, default=20)
    ap.add_argument("--name", default="", help="voice/speaker name written into the bank")
    args = ap.parse_args()

    profile = PROFILES / args.profile
    voice_dir = profile / "voice"
    if not voice_dir.is_dir():
        print(f"no voice folder: {voice_dir}")
        return 2
    clips = sorted(p for p in voice_dir.iterdir() if p.suffix.lower() in AUDIO_SUFFIXES)
    if not clips:
        print(f"no audio in {voice_dir}")
        return 2
    if not have("ffmpeg") or not have("ffprobe"):
        print("ffmpeg/ffprobe are required")
        return 2

    speaker = args.name or args.profile
    bank = profile / "voicebank"
    audio_dir = bank / "dataset" / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)

    rows, wavs = [], []
    for clip in clips:
        target = audio_dir / (clip.stem + ".wav")
        to_wav(clip, target)
        meta = probe(target)
        wavs.append((target, meta))
        rows.append({
            "file_name": f"audio/{target.name}",
            "transcript": transcript_hint(clip.name, speaker),
            "source": clip.name,
            "seconds": meta["seconds"],
            "mean_db": meta["mean_db"],
            "sha256": sha256(target),
            "sampling_rate": 16000,
        })
        print(f"  {clip.name[:52]:52s} {meta['seconds']:6.2f}s {meta['mean_db']:7.1f} dB")

    # HF datasets layout: one JSON object per line, no dependence on a python library to read it back
    meta_path = bank / "dataset" / "metadata.jsonl"
    meta_path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    (bank / "dataset" / "README.md").write_text(
        f"""---
license: other
task_categories:
- text-to-speech
language:
- en
tags:
- voice
- speaker
- {speaker}
---

# {speaker} — voice dataset

Audio for the `{args.profile}` profile, converted to 16 kHz mono PCM. One JSON object per line in
`metadata.jsonl`; the `transcript` field is derived from the clip filenames and is a hint, not a verified
transcript.
""", encoding="utf-8")

    # the artefact a zero-shot cloning model actually reads
    clean = sorted(wavs, key=lambda item: (-item[1]["mean_db"], -item[1]["seconds"]))
    chosen, total = [], 0.0
    for path, meta in clean:
        if total >= args.reference_seconds:
            break
        chosen.append(path)
        total += meta["seconds"]
    reference = bank / "reference.wav"
    if chosen:
        listing = bank / "dataset" / "_concat.txt"
        listing.write_text("".join(f"file '{p.resolve()}'\n" for p in chosen), encoding="utf-8")
        run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
             "-i", str(listing), "-t", str(args.reference_seconds), str(reference)])
        total = probe(reference)["seconds"]      # what -t actually wrote, not what the sources summed to
        listing.unlink(missing_ok=True)
        # the transcript of the reference is the concatenation of the clips it was cut from
        transcripts = [r["transcript"] for r in rows if any(c.name == Path(r["file_name"]).name for c in chosen)]
        (bank / "reference.txt").write_text(". ".join(t for t in transcripts if t) + ".\n", encoding="utf-8")

    durations = [m["seconds"] for _, m in wavs]
    loudness = [m["mean_db"] for _, m in wavs]
    speaker_json = {
        "speaker": speaker,
        "profile": args.profile,
        "clips": len(wavs),
        "total_seconds": round(sum(durations), 2),
        "mean_clip_seconds": round(sum(durations) / len(durations), 2),
        "mean_db": round(sum(loudness) / len(loudness), 2),
        "sample_rate": 16000,
        "reference": "reference.wav" if chosen else None,
        "reference_seconds": round(total, 2),
        "reference_sources": [p.name for p in chosen],
        "embedding": maybe_embedding([p for p, _ in wavs]),
        "model_hint": "FunAudioLLM/Fun-CosyVoice3-0.5B-2512",
        "note": "A zero-shot clone uses reference.wav, not averaged weights. The embedding, when present, is a voiceprint "
                "for comparison and fine-tuning.",
    }
    (bank / "speaker.json").write_text(json.dumps(speaker_json, indent=2) + "\n", encoding="utf-8")
    (bank / "config.json").write_text(json.dumps({
        "model_type": "voicebank",
        "speaker": speaker,
        "sample_rate": 16000,
        "reference_audio": "reference.wav",
        "reference_text": "reference.txt",
        "dataset": "dataset",
    }, indent=2) + "\n", encoding="utf-8")

    print(f"\nbank: {bank}")
    print(f"  clips {len(wavs)} · {sum(durations):.1f}s total · reference {total:.1f}s from {len(chosen)} clip(s)")
    print(f"  reference {'written' if chosen else 'NOT written'} · embedding {'yes' if speaker_json['embedding'] else 'no (no torch/speaker encoder)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
