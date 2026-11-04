"""The tiered runtime — loads a Hugging Face model across GPU, CPU and disk, and serves it as OpenAI.

Run by the **runtime** interpreter (torch/transformers/accelerate), never by Athena's own venv:

    python offload_runtime.py --model <dir> --plan <plan.json> --port 8080 --served-name qwen3.5-4b

`plan.json` comes from `models/tiering.py`, so the decision about what is hot was made by Athena and only
the execution happens here. Two consequences worth keeping: the agent needs no torch, and the placement
policy is testable without a GPU.

What accelerate does with that map:

    {"model.layers.0": 0, "model.layers.12": 0,           → resident in VRAM
     "model.layers.13": "cpu", "model.layers.20": "cpu",  → in RAM, full precision
     "model.layers.21": "disk", ...}                       → mmapped from the offload folder

The HTTP surface is deliberately small — `/v1/models`, `/v1/chat/completions`, `/health` — because
Athena's openai adapter is the client and every extra endpoint is surface without a reader.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time
from pathlib import Path

STARTED_AT = time.time()

# Tool calls arrive in whatever encoding the model's chat template taught it. All of these are real:
#
#   <tool_call>{"name": "x", "arguments": {...}}</tool_call>          Qwen2.5, Hermes
#   <tool_call><function=x><parameter=a>1</parameter></function></tool_call>   Qwen3.5
#   <function=x><parameter=a>1</parameter></function>                 Qwen3.5, unwrapped
#   [TOOL_CALLS] [{"name": "x", "arguments": {...}}]                  Mistral
#   <|python_tag|>{"name": "x", "parameters": {...}}                  Llama 3.1
#   ```json\n{"name": "x", "arguments": {...}}\n```                     DeepSeek-R1 distills
#   {"name": "x", "arguments": {...}}                                bare JSON (Functionary)
#
# Reading one and not the others leaves raw markup in the reply, which a user reads as "the agent
# printed gibberish at me". So: collect from every form, strip every span, and normalise the arguments
# key (models disagree between `arguments` and `parameters`).

# Tool-call parsing and thinking-strip live in tool_calls.py: one module, every encoding, testable
# without torch. This script is run as a file (sys.path[0] = providers/) and imported as a module when
# the multiplexer loads it, so both import paths are supported.
try:
    from providers.tool_calls import parse_tool_calls, strip_thinking
except ImportError:                                   # executed as a script
    from tool_calls import parse_tool_calls, strip_thinking


def budgets(plan: dict) -> dict:
    """Per-tier byte budgets for the loader.

    The GPU figure is deliberately **half** of what the card looks like it can spare: transformers'
    `caching_allocator_warmup` reserves roughly the GPU-side byte count again as a scratch buffer while
    loading, so a budget that fills the card to the brim OOMs at load with "tried to allocate N GiB" and
    names no module. Half a card of fast layers beats a load that fails.
    """
    hw = plan["hardware"]
    kv = plan["kv_cache_bytes"]
    gpu = max(0, int(hw["vram_free"] * plan.get("gpu_fill", 0.90) * 0.5) - 256 * 1024 ** 2)
    cpu = max(0, int(hw["ram_available"] * plan.get("ram_fill", 0.75)) - 768 * 1024 ** 2)
    disk = max(0, int(hw["disk_free"] * 0.5))
    return {"gpu": gpu, "cpu": cpu, "disk": disk, "kv": kv}


def admission_note(plan: dict, b: dict) -> str:
    """What the loader will actually admit, next to what the planner sized for.

    The two figures differ on purpose and always have: the planner fills the card to `gpu_fill` (90 %), the
    loader halves that to survive transformers' allocator warmup. Printing only the plan made the startup log
    claim `gpu 14 layers` while accelerate placed 5 — a log line that describes an intention, not the run.
    """
    gpu_plan = plan["tiers"]["gpu"]
    planned_bytes = gpu_plan["weights_bytes"] + gpu_plan["kv_bytes"]
    # No layer-count estimate: the budget also has to hold non-layer weights (embeddings, lm_head, vision
    # tower) and the KV cache, so dividing it by "weights per layer" produces a number that is simply wrong —
    # the first version claimed "~14 of 14" for a run that placed 5. The placed count is logged by accelerate
    # and by promotion; this line states only the two figures that are actually known.
    return (f"[offload] loader admits gpu {b['gpu'] / 1024 ** 3:.2f} GiB "
            f"(plan sized {planned_bytes / 1024 ** 3:.2f} GiB at gpu_fill={plan.get('gpu_fill')}) — "
            f"the difference is the allocator-warmup halving, recovered after load by promotion")


def _decoder_layers(model):
    """The transformer blocks, wherever this checkpoint keeps them.

    Multimodal wrappers nest them (`model.language_model.model.layers`), text models do not
    (`model.model.layers`). Both are tried rather than assumed — a wrong guess would silently promote nothing.
    """
    for path in (("model", "language_model", "layers"), ("model", "layers"),
                 ("language_model", "layers"), ("language_model", "model", "layers"),
                 ("transformer", "h")):
        node = model
        for step in path:
            node = getattr(node, step, None)
            if node is None:
                break
        # NOT isinstance(list/tuple): the blocks live in a torch.nn.ModuleList, which is a Module, not a
        # Python container — that check returned 0 layers for this checkpoint and promoted nothing
        if node is not None and hasattr(node, "__len__") and hasattr(node, "__getitem__") and len(node):
            return list(node)

    # last resort: find the ModuleList that is as long as the config says the model is deep. Robust across
    # architectures whose attribute names differ, and it cannot pick a block list of the wrong length.
    depth = (getattr(getattr(model, "config", None), "text_config", None)
             or getattr(model, "config", None))
    depth = getattr(depth, "num_hidden_layers", 0) or 0
    if depth:
        for _, module in model.named_modules():
            if hasattr(module, "__len__") and hasattr(module, "__getitem__") \
                    and len(module) == depth and not hasattr(module, "parameters"):
                return list(module)
    return []


def promote_layers(model, count: int) -> dict:
    """Move up to `count` CPU-resident decoder layers onto the GPU *after* loading.

    `budgets()` halves the GPU figure because transformers' `caching_allocator_warmup` reserves roughly the
    GPU-side bytes again while loading — a budget that fills the card OOMs at load. But that cost exists only
    during load: once the model is resident, the card has room again, and this closes the gap the planner and
    the loader disagreed about (planner sized 14 layers for a 90 % fill, the loader admitted 5 at 45 %).

    Every moved layer has its accelerate offload hook detached first: with a hook still attached the layer would
    be dispatched back to its hook's device at call time, silently undoing the move.
    """
    if count <= 0:
        return {"promoted": 0, "total": 0, "requested": count}
    try:
        from accelerate.hooks import remove_hook_from_module
    except Exception:                                 # noqa: BLE001 — accelerate is optional for this path
        return {"promoted": 0, "total": 0, "requested": count, "error": "accelerate hooks unavailable"}
    import torch
    layers = _decoder_layers(model)
    # the ceiling is not a layer count, it is what the KV cache will need: 14 promoted layers left 565 MiB
    # and generation OOMed on a 20 MiB allocation. Keep a reserve and stop before touching it.
    reserve = int(os.environ.get("ATHENA_PROMOTE_RESERVE_MB") or 1600) * 1024 ** 2
    promoted = 0
    skipped = {"cuda": 0, "meta": 0, "no-params": 0, "reserve": 0}
    for layer in layers:
        if promoted >= count:
            break
        # Classify by EVERY parameter, not the first one. `next(parameters())` returned a meta tensor for 30
        # layers whose weights were actually resident in RAM — one meta submodule (a rotary embed, a norm)
        # made an entire layer look disk-resident, so promotion skipped everything.
        devices = {str(prm.device) for prm in layer.parameters() if prm is not None}
        if not devices:
            skipped["no-params"] += 1
            continue
        if devices == {"meta"}:
            skipped["meta"] += 1
            continue
        if all(d.startswith("cuda") for d in devices):
            skipped["cuda"] += 1
            continue
        # a mixed layer (some real weights, some meta) is still worth moving: `layer.to("cuda")` carries the
        # real tensors and leaves the placeholders to their hooks, which is what makes promotion best-effort
        # rather than all-or-nothing
        # `meta` means the layer was offloaded to DISK and has no resident weights: moving it would either
        # raise or copy a placeholder. Skip it and keep going — promotion must never be the thing that breaks
        # the disk tier, and it must not stop promoting CPU-resident layers just because a disk one came first.

        try:
            layer_bytes = sum(p.numel() * p.element_size() for p in layer.parameters() if p is not None)
            free, _ = torch.cuda.mem_get_info()
            if free - layer_bytes < reserve:
                # print the PROJECTED figure too: "1768 MiB free, reserve 1600" looks like a bug, because the
                # decision is about free AFTER the move (1768 - 264 = 1504 < 1600), not free before it
                skipped["reserve"] += 1
                print(f"[offload] promotion stopped at {promoted}: {free / 1024**2:.0f} MiB free now, "
                      f"{layer_bytes / 1024**2:.0f} MiB needed, would leave "
                      f"{(free - layer_bytes) / 1024**2:.0f} MiB (reserve {reserve / 1024**2:.0f} MiB)",
                      flush=True)
                break
            remove_hook_from_module(layer, recurse=True)
            layer.to("cuda:0")
            promoted += 1
        except Exception as exc:                      # noqa: BLE001 — OOM here just stops promotion
            print(f"[offload] promotion stopped after {promoted}: {type(exc).__name__}: {exc}", flush=True)
            break
    print(f"[offload] promoted {promoted}/{len(layers)} CPU layers to the GPU (requested {count}); "
          f"skipped: {skipped}", flush=True)
    return {"promoted": promoted, "total": len(layers), "requested": count}


def _model_classes():
    """Every loader that can hold this checkpoint, best first.

    `Qwen3_5ForConditionalGeneration` is a multimodal class, so a plain `AutoModelForCausalLM` refuses
    it. Trying in order keeps one runtime for text-only and vision-capable checkpoints alike.
    """
    import transformers
    out = []
    for name in ("AutoModelForImageTextToText", "AutoModelForVision2Seq", "AutoModelForCausalLM"):
        cls = getattr(transformers, name, None)
        if cls is not None:
            out.append(cls)
    return out


def load_tiered(model_dir: str, plan: dict, offload_folder: Path):
    """Load across the three tiers by capacity: `device_map="auto"` plus a `max_memory` that has disk.

    Hand-building a device map was wrong twice over: checkpoint key names need not match the loaded
    module tree, and accelerate answers an unresolvable map by putting *everything* on CPU with only a
    warning — a server that answers and is 30x slower. `max_memory` with a `"disk"` entry is the
    supported way to say "GPU first, then RAM, then disk", and the loader fills it in that order.
    """
    from transformers import AutoConfig

    b = budgets(plan)
    print(admission_note(plan, b), flush=True)
    weights_total = int(plan.get("weights_bytes") or 0)
    # The disk tier is DYNAMIC, not always-on. Offering it when RAM already holds the weights makes
    # accelerate stash the state dict in the offload folder, which leaves CPU modules as `meta` placeholders —
    # the model runs, but nothing is resident to promote to the GPU, so steady-state speed silently drops.
    needs_disk = bool(plan["tiers"]["disk"]["layers"]) or weights_total > b["cpu"]
    max_memory: dict = {0: b["gpu"], "cpu": b["cpu"]}
    if needs_disk:
        max_memory["disk"] = b["disk"]
    print(f"[offload] disk tier: {'offered' if needs_disk else 'not needed'} "
          f"(weights {weights_total / 1024 ** 3:.2f} GiB vs cpu budget {b['cpu'] / 1024 ** 3:.2f} GiB)",
          flush=True)
    kwargs: dict = {"device_map": "auto", "max_memory": max_memory,
                    "low_cpu_mem_usage": True, "trust_remote_code": True}
    if needs_disk:
        kwargs["offload_folder"] = str(offload_folder)
        # the state dict only leaves RAM when it has to
        kwargs["offload_state_dict"] = True

    try:
        AutoConfig.from_pretrained(model_dir, trust_remote_code=True)
    except OSError as exc:
        raise RuntimeError(f"{model_dir}: not a loadable checkpoint: {exc}") from exc

    last_error = None
    for cls in _model_classes():
        try:
            model = cls.from_pretrained(model_dir, **kwargs)
            hf = getattr(model, "hf_device_map", None) or {}
            from collections import Counter
            print(f"[offload] loaded with {cls.__name__}; devices "
                  f"{dict(Counter(str(v) for v in hf.values()))}", flush=True)
            return model
        except (TypeError, ValueError, OSError, RuntimeError) as exc:
            last_error = exc
            continue
    raise RuntimeError(f"no loader could place this model: {last_error}")


def normalise_messages(messages: list[dict]) -> list[dict]:
    """Make tool calls acceptable to a Jinja chat template.

    The OpenAI wire format sends `arguments` as a JSON **string**; Qwen's template iterates it as a
    mapping (`arguments|items`), so a string raises "Can only get item pairs from a mapping" — and only
    once a tool call is in the history, which is why the first turn works and the second does not.
    """
    out = []
    for message in messages:
        message = dict(message)
        calls = message.get("tool_calls")
        if calls:
            fixed = []
            for call in calls:
                call = dict(call)
                fn = dict(call.get("function") or {})
                if not fn.get("name") and call.get("name"):
                    fn["name"] = call["name"]
                arguments = fn.get("arguments", call.get("arguments"))
                if isinstance(arguments, str):
                    try:
                        arguments = json.loads(arguments) if arguments.strip() else {}
                    except json.JSONDecodeError:
                        arguments = {"_raw": arguments}
                fn["arguments"] = arguments if isinstance(arguments, dict) else {"_raw": arguments}
                call["function"] = fn
                call.setdefault("type", "function")
                fixed.append(call)
            message["tool_calls"] = fixed
        out.append(message)
    return out


class _FirstTokenTimer:
    """Records when the first output token is produced, i.e. when prefill finished.

    A logits processor runs once per generated token, so `.first` is stamped at the end of prefill without
    touching the generate() call. Without this split, "the turn took 5 s" says nothing about whether the prompt
    or the generation is paying — and the whole case for prefix reuse rests on that split.
    """

    def __init__(self):
        self.first: float | None = None
        self.started: float | None = None

    def __call__(self, input_ids, scores):
        if self.first is None:
            self.first = time.time()
        return scores


class _FirstTokenTimerDecorator:
    """Adapter: transformers calls `__call__(input_ids, scores)`; ours records and returns scores unchanged."""

    def __init__(self, timer: _FirstTokenTimer):
        self.timer = timer

    def __call__(self, input_ids, scores):
        return self.timer(input_ids, scores)


def build_app(model_dir: str, plan_path: str, served_name: str):
    import torch
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse
    from transformers import AutoTokenizer

    plan = json.loads(Path(plan_path).read_text(encoding="utf-8"))
    offload_folder = Path(plan_path).parent / "offload"
    offload_folder.mkdir(parents=True, exist_ok=True)

    tokenizer = AutoTokenizer.from_pretrained(model_dir, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    model = load_tiered(model_dir, plan, offload_folder)
    model.eval()

    # steady-state promotion, opt-in: the load budget is halved for the allocator warmup, which no longer
    # applies once the model is resident. 0 (the default) keeps the previous behaviour exactly.
    promote_n = int(os.environ.get("ATHENA_PROMOTE_LAYERS") or plan.get("promote_layers") or 0)
    promotion = promote_layers(model, promote_n)

    hf_map = getattr(model, "hf_device_map", None) or {}
    placed = {str(k): str(v) for k, v in hf_map.items()}
    from collections import Counter
    actual = Counter(str(v) for v in hf_map.values())

    from core import version as _version

    app = FastAPI(title="athena offload runtime", version=_version.text())

    @app.get("/health")
    def health():
        return {"status": "ok", "served_name": served_name,
                "pid": os.getpid(),
                "started_at": STARTED_AT,
                "runtime_mtime": Path(__file__).stat().st_mtime,
                "planned": plan["tiers"], "actual_devices": dict(actual),
                "promotion": promotion,
                "placement": placed}

    @app.get("/v1/models")
    def models():
        return {"object": "list",
                "data": [{"id": served_name, "object": "model", "owned_by": "local"}]}

    @app.post("/v1/chat/completions")
    def chat(body: dict):
        started = time.time()
        messages = body.get("messages") or []
        tools = body.get("tools") or None
        max_new = int(body.get("max_tokens") or 512)
        temperature = float(body.get("temperature") if body.get("temperature") is not None else 0.7)

        # Templates that insist "system must be at the beginning" mean one system turn, first, and no
        # others anywhere. Athena can produce a stray system turn (a recorded API failure, for example),
        # so merge every system message into the leading one instead of letting the template 500.
        roles = [m.get("role") for m in messages]
        if "system" in roles[1:]:
            head = "\n\n".join(m.get("content") or "" for m in messages if m.get("role") == "system")
            rest = [m for m in messages if m.get("role") != "system"]
            messages = [{"role": "system", "content": head}] + rest
            print(f"[offload] merged stray system turns; roles were {roles}", flush=True)

        messages = normalise_messages(messages)
        think = body.get("chat_template_kwargs") or {}
        if "enable_thinking" not in think:
            think = {"enable_thinking": False}      # an agent wants the answer, not the deliberation
        try:
            text = tokenizer.apply_chat_template(
                messages, tools=tools, add_generation_prompt=True, tokenize=False, **think)
        except TypeError:
            text = tokenizer.apply_chat_template(
                messages, tools=tools, add_generation_prompt=True, tokenize=False)
        except ValueError:
            text = tokenizer.apply_chat_template(
                messages, add_generation_prompt=True, tokenize=False)
            tools = None                              # template cannot render tools; answer in prose
        except Exception as exc:                      # noqa: BLE001 — report, do not 500 blindly
            print(f"[offload] template error: {type(exc).__name__}: {exc} | roles={roles}", flush=True)
            return JSONResponse({"error": {"message": f"{type(exc).__name__}: {exc}",
                                           "roles": roles}}, status_code=400)
        prompt_started = time.time()
        inputs = tokenizer(text, return_tensors="pt", add_special_tokens=False).to(model.device)

        timer = _FirstTokenTimer()
        gen_kwargs = {"max_new_tokens": max_new, "pad_token_id": tokenizer.pad_token_id,
                      "logits_processor": [_FirstTokenTimerDecorator(timer)]}
        if temperature <= 0.01:
            gen_kwargs.update({"do_sample": False})
        else:
            gen_kwargs.update({"do_sample": True, "temperature": temperature,
                               "top_p": float(body.get("top_p") or 0.95)})
        prompt_tokens_measured = int(inputs["input_ids"].shape[-1])
        with torch.no_grad():
            out = model.generate(**inputs, **gen_kwargs)
        new_tokens = out[0][inputs["input_ids"].shape[-1]:]
        raw = tokenizer.decode(new_tokens, skip_special_tokens=True)

        prose, calls = parse_tool_calls(raw)
        msg: dict = {"role": "assistant", "content": prose}
        finish = "stop"
        if calls:
            msg["tool_calls"] = [{"id": c["id"], "type": "function",
                                  "function": {"name": c["name"],
                                               "arguments": json.dumps(c["arguments"])}}
                                 for c in calls]
            finish = "tool_calls"
        elif len(new_tokens) >= max_new:
            finish = "length"

        return JSONResponse({
            "id": f"chatcmpl-{int(started)}",
            "object": "chat.completion",
            "created": int(started),
            "model": served_name,
            "choices": [{"index": 0, "message": msg, "finish_reason": finish}],
            "usage": {"prompt_tokens": int(inputs["input_ids"].shape[-1]),
                      "completion_tokens": int(new_tokens.shape[-1]),
                      "total_tokens": int(inputs["input_ids"].shape[-1] + new_tokens.shape[-1])},
            "athena": {"seconds": round(time.time() - started, 3),
                       # prefill vs decode: the split that decides whether a prefix cache is worth building
                       "prefill_s": round((timer.first - prompt_started), 3) if timer.first else None,
                       "decode_s": round((time.time() - timer.first), 3) if timer.first else None,
                       "prompt_tokens": prompt_tokens_measured,
                       "decode_tokens_per_s": (round(int(new_tokens.shape[-1]) / (time.time() - timer.first), 3)
                                               if timer.first and new_tokens.shape[-1] else None),
                       "tiers": plan["tiers"]},
        })

    return app


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--plan", required=True)
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--served-name", default="local")
    args = ap.parse_args()

    import uvicorn
    app = build_app(args.model, args.plan, args.served_name)
    print(f"[offload] serving {args.served_name} on http://{args.host}:{args.port}/v1", flush=True)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
