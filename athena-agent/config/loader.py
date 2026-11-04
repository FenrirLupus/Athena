"""Config loading — defaults, user overrides, validation.

Rules (documentation/architecture/08-config.md):
  * defaults ship commented and are the documentation
  * a user value overrides a default key by key; a missing key falls back
  * every value is validated at load; an invalid file is refused with the key and the reason
  * unknown keys warn rather than crash
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

ATHENA_ROOT = Path(os.environ.get("ATHENA_ROOT") or (Path.home() / ".athena"))
AGENT_DIR = ATHENA_ROOT / "athena-agent"
DEFAULT_CONFIG = AGENT_DIR / "config" / "defaults.yaml"
USER_CONFIG = ATHENA_ROOT / "config.yaml"


class ConfigError(RuntimeError):
    """Raised when the config is present but invalid; carries the offending key."""


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path.name}: invalid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path.name}: top level must be a mapping")
    return data


def _merge(base: dict, override: dict, path: str = "") -> dict:
    """Deep-merge override onto base, returning a NEW dict (never aliasing base)."""
    out = {}
    for key, val in base.items():
        if isinstance(val, dict):
            out[key] = _merge(val, override.get(key, {}) if isinstance(override.get(key), dict) else {}, f"{path}{key}.")
        else:
            out[key] = override.get(key, val)
    for key, val in override.items():
        if key not in base:
            out[key] = val
    return out


def _unknown_keys(base: dict, user: dict, warnings: list[str], path: str = "") -> None:
    for key, val in user.items():
        if key not in base:
            warnings.append(f"unknown config key: {path}{key}")
        elif isinstance(val, dict) and isinstance(base.get(key), dict):
            _unknown_keys(base[key], val, warnings, f"{path}{key}.")


PROVIDER_FORMATS = {"openai", "anthropic", "mock", "deepseek", "lmstudio", "llamacpp",
                    "ollama", "vllm", "custom", "claude"}
LOCAL_BACKENDS = {"transformers", "vllm", "llamacpp", "command"}


def _validate(cfg: dict) -> None:
    model = cfg.get("model") or {}
    fmt = (model.get("provider") or "mock").lower()
    if fmt not in PROVIDER_FORMATS:
        raise ConfigError(
            f"model.provider: {fmt!r} is not a wire format — use openai, anthropic or mock")

    local = model.get("local") or {}
    if local.get("enabled"):
        backend = (local.get("backend") or "llamacpp").lower()
        if backend not in LOCAL_BACKENDS:
            raise ConfigError(
                f"model.local.backend: {backend!r} — use transformers, vllm, llamacpp or command")
        if backend == "command" and not str(local.get("command") or "").strip():
            raise ConfigError("model.local.command: required when backend is command")
        if backend in ("llamacpp", "vllm", "transformers") and not str(local.get("name") or local.get("repo") or "").strip():
            raise ConfigError(
                "model.local.name: required — name a downloaded model (athena models list) or set local.repo")
        if int(local.get("port") or 0) <= 0:
            raise ConfigError("model.local.port: must be a positive port number")
        # a local server truncates the prompt past its own ctx, so ctx must cover the window the stack
        # is sized against — comparing to model.window (not a literal) keeps this true at 64K and 128K
        ctx = int(local.get("ctx") or 0)
        window = int(model.get("window") or 0)
        try:
            from core.context_window import resolve as _resolve_window
            window = int(_resolve_window(model).get("window") or window)
        except Exception:                          # noqa: BLE001 — a broken window block is reported elsewhere
            pass

        if ctx and ctx < window:
            raise ConfigError(
                f"model.local.ctx: {ctx} is below model.window ({window}) — the server would silently "
                f"truncate the front of the prompt, which looks exactly like the agent forgetting")
    window = int(model.get("window") or 0)
    if window < 32768:
        raise ConfigError(
            f"model.window: {window} is below the 32,768 minimum — the prompt stack cannot hold a conversation")
    local_floor = model.get("context_window_local")
    if local_floor not in (None, "", 0):
        floor = int(local_floor)
        if floor < 32768:
            raise ConfigError(
                f"model.context_window_local: {floor} is below the 32,768 minimum — it is the value the effective "
                f"window falls back to, so a smaller floor silently shrinks the prompt stack")
    out_tok = int(model.get("max_output_tokens") or 0)
    if out_tok <= 0 or out_tok >= window:
        raise ConfigError(
            f"model.max_output_tokens: {out_tok} must be > 0 and below the window ({window})")

    budget = cfg.get("budget") or {}
    pcts = {k: float(budget.get(k, 0.0)) for k in
            ("system_pct", "assistant_pct", "user_pct", "summary_pct", "history_pct",
             "memory_pct", "output_pct")}
    for key, val in pcts.items():
        if val < 0 or val > 100:
            raise ConfigError(f"budget.{key}: {val} is out of range 0-100")
    total = sum(pcts.values())
    if total > 100.0:
        raise ConfigError(
            f"budget: block shares plus output_pct ({total:.1f}%) exceed the window — lower a share or raise model.window")
    if pcts["summary_pct"] > pcts["history_pct"]:
        raise ConfigError(
            f"budget.summary_pct ({pcts['summary_pct']}) must not exceed budget.history_pct "
            f"({pcts['history_pct']}) — the digest would cost more than the history it replaces")

    for key in ("max_iterations",):
        if int(budget.get(key, 0)) <= 0:
            raise ConfigError(f"budget.{key}: must be positive")


def render_full(effective: dict, template: Path | None = None) -> str:
    """A commented config file with **every** setting populated from `effective`.

    The file is the whole surface: every key an operator can see, change or break is present with the value
    currently in force, whether the file is being created or refreshed after an update. A file that lists only
    overrides hides the rest of the settings from the person expected to tune them.
    """
    from core.agentconfig import _fill          # the same comment-preserving filler the agent files use

    template = template or (Path(__file__).resolve().parent / "defaults.yaml")
    return _fill(template.read_text(encoding="utf-8"), effective)


def write_full(effective: dict, path: Path | None = None) -> Path:
    """Write the effective config to `path` (default: the user's config.yaml), fully populated."""
    root = Path(os.environ.get("ATHENA_ROOT") or (Path.home() / ".athena"))
    target = path or (root / "config.yaml")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_full(effective), encoding="utf-8")
    return target


def load() -> dict:
    """Defaults <- user config, validated. Returns the effective config."""
    base = _load_yaml(DEFAULT_CONFIG)
    if not base:
        raise ConfigError(f"missing defaults at {DEFAULT_CONFIG}")
    user = _load_yaml(USER_CONFIG)
    warnings: list[str] = []
    _unknown_keys(base, user, warnings)
    cfg = _merge(base, user)
    _validate(cfg)
    cfg["_warnings"] = warnings
    cfg["_athena_root"] = str(ATHENA_ROOT)
    return cfg


def paths(cfg: dict) -> dict:
    """Resolved filesystem locations used across the system."""
    root = ATHENA_ROOT
    return {
        "root": root,
        "agents": root / str((cfg.get("agents") or {}).get("dir") or "profiles"),
        "sessions_rel": "sessions",
    }
