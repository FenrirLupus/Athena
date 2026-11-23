"""The write guard — a config that cannot be read back is not written.

The failure this exists for: a lane `config.yaml` that did not parse. `agentconfig.read()` caught the YAML error
and returned `{}`, so the lane silently had no model, and the operator was told "no model named for this channel"
about a file that plainly named one. Nothing checked the text before it landed, so the bad file was written by a
program, read by a program, and diagnosed by nobody.

Two rules, and they are the whole module:

  * a write whose text does not parse is refused, and the file on disk is untouched;
  * a write that *repairs* an already-broken file leaves the broken text behind as `<name>.broken-<stamp>`,
    because the evidence is what explains the outage.

The parser here is `yaml.safe_load` — the same one `agentconfig.read()` and `loader._load_yaml()` use — so
"parses here" cannot disagree with "parses there". Duplicate keys are checked too: PyYAML accepts them, so a file
can parse and still not read back as written.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from core import durable
from core import timefmt
from core import trace

__all__ = ["WriteRefused", "parse_report", "guard"]


class WriteRefused(RuntimeError):
    """The text asked for cannot be read back. Distinct from `ConfigError` (the file on disk cannot be loaded)."""


def _duplicates(text: str) -> list[str]:
    """Repeated keys in one mapping, at any depth. PyYAML keeps the last one, so a parse alone would not show it."""
    try:
        node = yaml.compose(text)
    except yaml.YAMLError:
        return []
    found: list[str] = []

    def walk(current) -> None:
        if isinstance(current, yaml.MappingNode):
            seen: set[str] = set()
            for key_node, value_node in current.value:
                key = str(getattr(key_node, "value", ""))
                if key in seen and key not in found:
                    found.append(key)
                seen.add(key)
                walk(value_node)
        elif isinstance(current, yaml.SequenceNode):
            for item in current.value:
                walk(item)

    if node is not None:
        walk(node)
    return sorted(found)


def parse_report(text: str, path="") -> dict:
    """`{"ok", "error", "line", "duplicates"}` — never raises, never guesses a line it was not given.

    A *character* error (`unacceptable character #x0001`) carries no mark, so `line` is None there and the
    message must not invent one.
    """
    try:
        yaml.safe_load(text)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        line = int(mark.line) + 1 if mark is not None else None
        problem = getattr(exc, "problem", None) or str(exc).splitlines()[0]
        return {"ok": False, "error": str(problem), "line": line, "duplicates": []}
    return {"ok": True, "error": "", "line": None, "duplicates": _duplicates(text)}


def _message(path, report: dict, *, note: str = "") -> str:
    where = f"{path}: " if path else ""
    if report.get("line"):
        tail = f"line {report['line']}: {report['error']}"
    elif report.get("error"):
        tail = f"{report['error']} (no line reported)"
    else:
        tail = "duplicate key introduced"
    return f"config write refused: {where}{tail}{note} — the file on disk is unchanged"


def guard(text: str, path, *, preimage: str | None = None) -> dict:
    """Refuse a write whose text would not be readable; copy the pre-image aside when repairing.

    Returns the parse report on success. Raises `WriteRefused` otherwise, having written nothing — it runs
    before any atomic write, so there is no temp file to leak.
    """
    target = Path(path)
    report = parse_report(text, target)
    old = parse_report(preimage, target) if preimage is not None else None
    old_duplicates = set((old or {}).get("duplicates") or [])
    introduced = [key for key in (report.get("duplicates") or []) if key not in old_duplicates]

    if report["ok"] and not introduced:
        if preimage is not None and old is not None and not old["ok"]:
            # a repair: the broken text is evidence, so it is copied before the good file replaces it
            stamp = timefmt.stamp().replace(":", "").replace("-", "")
            copy = target.with_name(f"{target.name}.broken-{stamp}")
            try:
                durable.write_state(copy, preimage)
            except OSError as exc:                  # a failed pre-image copy must not block the repair
                trace.failure("config.write.preimage.failed", exc, path=str(target), copy=str(copy))
            else:
                trace.event("config.write.repair", level="info", path=str(target),
                            old_line=old.get("line"), old_error=str(old.get("error"))[:160],
                            preimage=str(copy))
        return report

    if not report["ok"]:
        message = _message(target, report)
        if old is not None and not old["ok"]:
            message += (f" (the file on disk was already unreadable: line {old.get('line')}: "
                        f"{str(old.get('error'))[:120]})")
    else:
        report["error"] = f"duplicate key: {', '.join(introduced)}"
        message = _message(target, report, note=f" (duplicate key introduced: {', '.join(introduced)})")

    trace.failure("config.write.refused", None, path=str(target), line=report.get("line"),
                  rule="would-not-parse" if not report["ok"] else "duplicate-key", bytes=len(text or ""))
    raise WriteRefused(message)
