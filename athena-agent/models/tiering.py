"""Tiering — decide what lives on the GPU, what spills to RAM, and what stays on disk.

A 4B model in bf16 is ~8 GB. An 8 GB card also wants room for the KV cache and the desktop, so the
weights *cannot* all be resident: something has to decide, by measurement, which layers are fast and
which are merely available.

    GPU  (VRAM)   as many layers as fit after the KV cache and a safety reserve
    CPU  (RAM)    the next layers, full precision, nothing evicted
    disk (NVMe)   the remainder, memory-mapped through accelerate's offload folder

It is always better to be explicit than to let the loader OOM: `accelerate` will happily try to place
everything on the card and die at 97 %, and an OOM at load time tells you nothing about *which* tier
should have held what.

The unit is **one transformer block**: blocks are what a device map addresses, and they are why the
split is a prefix — layers 0..n are the hot ones, and a layer boundary is the only place a split does
not cost a round-trip per token.
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from core.root import athena_root
from core.jsonio import read_json
from core import trace

GIB = 1024 ** 3
MIB = 1024 ** 2


# -- measuring -------------------------------------------------------------

def probe_hardware() -> dict:
    """VRAM, RAM and disk, in bytes. Missing sources report 0 rather than a guess."""
    vram_total = vram_free = 0
    try:
        import subprocess
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total,memory.used", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10).stdout.strip().splitlines()
        if out:
            total_mib, used_mib = (int(x) for x in out[0].split(",")[:2])
            vram_total = total_mib * MIB
            vram_free = max(0, (total_mib - used_mib)) * MIB
    except (OSError, ValueError, subprocess.SubprocessError):
        pass

    ram_total = ram_available = 0
    try:
        with open("/proc/meminfo", encoding="utf-8") as fh:
            info = {line.split(":")[0]: int(line.split()[1]) * 1024
                    for line in fh if ":" in line and line.split()[1].isdigit()}
        ram_total = info.get("MemTotal", 0)
        ram_available = info.get("MemAvailable", info.get("MemFree", 0))
    except (OSError, ValueError, IndexError):
        pass

    disk_free = 0
    try:
        root = athena_root()
        disk_free = shutil.disk_usage(root).free
    except OSError:
        pass

    return {"vram_total": vram_total, "vram_free": vram_free,
            "ram_total": ram_total, "ram_available": ram_available, "disk_free": disk_free}


# -- reading a model -------------------------------------------------------

def read_config(model_dir: Path) -> dict:
    try:
        return json.loads((model_dir / "config.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def text_config(cfg: dict) -> dict:
    """The language model's config.

    A multimodal checkpoint (`Qwen3_5ForConditionalGeneration`, `...ForVision2Seq`) nests the text model
    under `text_config`/`llm_config`. Reading only the top level finds no layers at all and the planner
    silently reports "cannot size the model" — so unwrap it once, here.
    """
    for key in ("text_config", "llm_config", "language_config"):
        inner = cfg.get(key)
        if isinstance(inner, dict) and inner:
            merged = dict(inner)
            for k in ("num_hidden_layers", "hidden_size", "num_attention_heads", "num_key_value_heads"):
                if k in cfg and k not in merged:
                    merged[k] = cfg[k]
            return merged
    return cfg


def weights_bytes(model_dir: Path) -> int:
    """Total weight bytes, from the safetensors files themselves."""
    total = 0
    for f in model_dir.glob("*.safetensors"):
        try:
            total += f.stat().st_size
        except OSError:
            pass
    if total:
        return total
    idx = model_dir / "model.safetensors.index.json"
    meta = (read_json(idx, {}) or {}).get("metadata") or {}
    try:
        return int(meta.get("total_size") or 0)
    except (TypeError, ValueError):
        return 0


def layer_share(model_dir: Path, prefix: str) -> float:
    """Fraction of weight bytes that live in transformer blocks.

    A multimodal checkpoint carries a vision tower that never becomes a device-map layer, so dividing
    total bytes by layer count over-charges every layer and under-fills the GPU. Count tensors instead.
    """
    try:
        keys = list(json.loads((model_dir / "model.safetensors.index.json")
                               .read_text(encoding="utf-8"))["weight_map"])
    except (OSError, json.JSONDecodeError, KeyError):
        return 1.0
    if not keys:
        return 1.0
    in_layers = sum(1 for k in keys if re.match(rf"^{re.escape(prefix)}\.\d+\.", k))
    return (in_layers / len(keys)) if in_layers else 1.0


def layer_prefixes(model_dir: Path) -> tuple[str, int]:
    """('model.layers', 48) — the module prefix a device map should split on, and how many there are."""
    cfg = text_config(read_config(model_dir))
    n = int(cfg.get("num_hidden_layers") or cfg.get("n_layer") or 0)
    prefix = "model.layers"
    for idx in ("model.safetensors.index.json",):
        # the index is authoritative: it is what accelerate walks when it dispatches modules
        try:
            keys = list(json.loads((model_dir / idx).read_text(encoding="utf-8"))["weight_map"])
        except (OSError, json.JSONDecodeError, KeyError):
            continue
        for key in keys:
            m = re.match(r"^(.*\.layers)\.\d+\.", key)
            if m:
                prefix = m.group(1)
                break
    return prefix, n


def kv_cache_bytes(cfg: dict, window: int, dtype_bytes: int = 2) -> int:
    """KV cache for the full window: 2 (K,V) × layers × kv_heads × head_dim × bytes."""
    layers = int(cfg.get("num_hidden_layers") or 0)
    heads = int(cfg.get("num_key_value_heads") or cfg.get("num_attention_heads") or 0)
    hidden = int(cfg.get("hidden_size") or 0)
    attn_heads = int(cfg.get("num_attention_heads") or 0)
    head_dim = int(cfg.get("head_dim") or (hidden // attn_heads if attn_heads else 0))
    if not all((layers, heads, head_dim)):
        return 0
    return 2 * layers * heads * head_dim * window * dtype_bytes


# -- the plan --------------------------------------------------------------

def plan(model_dir: str | Path, window: int, *, gpu_fill: float = 0.90,
         ram_fill: float = 0.75, reserve_mb: int = 512, dtype_bytes: int = 2,
         hw: dict | None = None) -> dict:
    """Place the model across GPU, CPU and disk. Returns the plan plus the reasoning behind it."""
    model_dir = Path(model_dir)
    with trace.span("tier.plan", level="info", model=model_dir.name, window=int(window),
                    gpu_fill=gpu_fill, ram_fill=ram_fill) as sp:
        out = _plan(model_dir, window, gpu_fill=gpu_fill, ram_fill=ram_fill,
                    reserve_mb=reserve_mb, dtype_bytes=dtype_bytes, hw=hw)
        sp.ok(layers=out["layers"],
              gpu=out["tiers"]["gpu"]["layers"], cpu=out["tiers"]["cpu"]["layers"],
              disk=out["tiers"]["disk"]["layers"],
              weights_gib=round(out["weights_bytes"] / GIB, 2),
              kv_gib=round(out["kv_cache_bytes"] / GIB, 2))
    return out


def _plan(model_dir: Path, window: int, *, gpu_fill: float = 0.90,
          ram_fill: float = 0.75, reserve_mb: int = 512, dtype_bytes: int = 2,
          hw: dict | None = None) -> dict:
    model_dir = Path(model_dir)
    hw = hw or probe_hardware()
    trace.event("tier.hardware", level="debug", model=model_dir.name,
                vram_free_gib=round(hw["vram_free"] / GIB, 2), vram_total_gib=round(hw["vram_total"] / GIB, 2),
                ram_available_gib=round(hw["ram_available"] / GIB, 2),
                disk_free_gib=round(hw["disk_free"] / GIB, 2),
                measured=bool(hw["vram_total"] or hw["ram_total"]))
    cfg = read_config(model_dir)
    total = weights_bytes(model_dir)
    prefix, n_layers = layer_prefixes(model_dir)
    if not total or not n_layers:
        trace.event("tier.refused", level="error", model=model_dir.name,
                    weights_bytes=total, layers=n_layers,
                    reason="cannot size the model — no weights or no layer count")
        raise RuntimeError(f"{model_dir}: cannot size the model (weights={total}, layers={n_layers})")

    share = layer_share(model_dir, prefix)
    per_layer = (total * share) / n_layers if n_layers else 0
    kv = kv_cache_bytes(text_config(cfg), window, dtype_bytes)
    reserve = reserve_mb * MIB

    # Embedding and output head are usually *not* layers and can be several hundred MB on a big vocab;
    # they have to sit somewhere, so charge them to the GPU before handing out layer slots.
    # Only the TEXT-side non-layer weights have to be hot: embeddings plus lm_head when untied. The
    # vision tower of a multimodal checkpoint is never touched by a text turn, so charging all non-layer
    # bytes to the GPU (the first version did) left zero room and planned a fully CPU model.
    tc = text_config(cfg)
    vocab = int(tc.get("vocab_size") or 0)
    hidden = int(tc.get("hidden_size") or 0)
    if vocab and hidden:
        embeds = vocab * hidden * dtype_bytes
        if not tc.get("tie_word_embeddings", True):
            embeds *= 2
        fixed = min(embeds, total * 0.35)
    else:
        fixed = min(total * (1 - share), total * 0.20)

    # Capacity has to be solved per layer, not per model: a block costs its weights PLUS the slice of
    # KV cache that its own device must hold. Charging the whole cache to the GPU (the first version)
    # plans a 4B model entirely on CPU on a card that could hold a third of it.
    kv_per_layer = kv / n_layers if n_layers else 0
    per_layer_total = per_layer + kv_per_layer

    gpu_capacity = max(0.0, hw["vram_free"] * gpu_fill - reserve - fixed)
    gpu_layers = min(n_layers, int(gpu_capacity // per_layer_total)) if per_layer_total else 0

    cpu_capacity = max(0.0, hw["ram_available"] * ram_fill - reserve)
    cpu_layers = min(n_layers - gpu_layers,
                     int(cpu_capacity // per_layer_total)) if per_layer_total else 0

    disk_layers = n_layers - gpu_layers - cpu_layers

    # one line per tier that took layers: which tier, how many blocks, and the number that put them there
    for tier, count, why in (
        ("gpu", gpu_layers, f"{hw['vram_free'] / GIB:.1f} GiB free × {gpu_fill:.0%} after embeddings "
                            f"{fixed / GIB:.2f} GiB + reserve"),
        ("cpu", cpu_layers, f"{hw['ram_available'] / GIB:.1f} GiB available × {ram_fill:.0%} after reserve"),
        ("disk", disk_layers, f"the remainder — {total / GIB:.1f} GiB weights + {kv / GIB:.1f} GiB cache "
                              f"do not fit in VRAM and RAM"),
    ):
        if count:
            trace.event("tier.chosen", level="debug", model=model_dir.name, tier=tier,
                        layers=count, bytes=int(count * per_layer_total), reason=why)
    if gpu_layers == 0:
        # not a failure, but the decision that makes a 4B model slow: say it at info, with the shortfall
        trace.event("tier.refused", level="info", model=model_dir.name, tier="gpu", layers=0,
                    vram_free_gib=round(hw["vram_free"] / GIB, 2),
                    fixed_gib=round(fixed / GIB, 2),
                    per_layer_gib=round(per_layer_total / GIB, 3),
                    reason="VRAM free holds no block after the embeddings and the reserve")

    device_map: dict[str, object] = {}
    for i in range(n_layers):
        module = f"{prefix}.{i}"
        if i < gpu_layers:
            device_map[module] = 0
        elif i < gpu_layers + cpu_layers:
            device_map[module] = "cpu"
        else:
            device_map[module] = "disk"

    reasons = []
    if gpu_layers == 0:
        reasons.append(f"VRAM free {hw['vram_free'] / GIB:.1f} GiB holds no block after the embeddings "
                       f"({fixed / GIB:.2f} GiB) and reserve — this model runs entirely on CPU")
    if disk_layers:
        reasons.append(f"{disk_layers} layer(s) stay on disk — VRAM and RAM together do not cover "
                       f"{total / GIB:.1f} GiB of weights plus {kv / GIB:.1f} GiB of cache")
    if kv > hw["vram_total"] * 0.5:
        reasons.append(f"KV cache at {window} tokens is {kv / GIB:.1f} GiB — a shorter window would "
                       f"free VRAM for more layers")

    def tier_bytes(n: int) -> dict:
        return {"layers": n,
                "weights_bytes": int(n * per_layer),
                "kv_bytes": int(n * kv_per_layer),
                "total_bytes": int(n * per_layer_total)}

    return {
        "model_dir": str(model_dir),
        "layers": n_layers,
        "layer_prefix": prefix,
        "weights_bytes": total,
        "layer_share": round(share, 4),
        "per_layer_bytes": int(per_layer),
        "kv_cache_bytes": kv,
        "tiers": {"gpu": tier_bytes(gpu_layers), "cpu": tier_bytes(cpu_layers),
                  "disk": tier_bytes(disk_layers)},
        # best-effort for display; the runtime rebuilds the map from the checkpoint's real key names
        "device_map": device_map,
        "hardware": hw,
        "gpu_fill": gpu_fill,
        "ram_fill": ram_fill,
        "notes": reasons,
    }


def describe(plan_dict: dict) -> str:
    """One-line-per-tier human summary for the CLI and the logs."""
    t = plan_dict["tiers"]
    lines = [f"{plan_dict['layers']} layers · {plan_dict['weights_bytes'] / GIB:.2f} GiB weights · "
             f"KV {plan_dict['kv_cache_bytes'] / GIB:.2f} GiB"]
    for tier in ("gpu", "cpu", "disk"):
        info = t[tier]
        if info["layers"]:
            lines.append(f"  {tier:<5} {info['layers']:>3} layers  "
                         f"{info['weights_bytes'] / GIB:>5.2f} GiB weights "
                         f"+ {info['kv_bytes'] / GIB:>4.2f} GiB cache")
    for note in plan_dict.get("notes") or []:
        lines.append(f"  note: {note}")
    return "\n".join(lines)
