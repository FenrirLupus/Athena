#!/usr/bin/env python3
"""Manage tools: create a package, list what is registered, remove one, enable or disable it.

A tool is a package — TOOL.md plus scripts/<name>.py. This script writes, reads and removes packages in the agent's
own tools root and toggles them in that agent's config. It refuses to touch the program tree: what ships is not the
agent's to edit.
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from core import trace

NAME = re.compile(r"^[a-z][a-z0-9-]{1,48}$")

SCAFFOLD = '''#!/usr/bin/env python3
"""{description}"""
import json
import sys


def main(args: dict) -> dict:
    verb = str(args.get("sub") or "").strip()
    if verb != "{verb}":
        raise RuntimeError("unknown sub-action {{verb!r}}; use {verb}")
    return {{"ok": True, "echo": args}}


if __name__ == "__main__":
    payload = json.loads(sys.argv[1] if len(sys.argv) > 1 else "{{}}")
    print(json.dumps(main(payload), indent=1))
'''


def _roots(ctx) -> tuple[Path, Path | None]:
    from tools import loader
    profile, athena = loader.agent_roots(ctx)
    return Path(profile), (Path(athena) if athena else None)


def _program() -> Path:
    return Path(__file__).resolve().parents[3]


def _refuse_program(path: Path) -> None:
    """Anything inside the shipped tree is read-only to an agent."""
    try:
        path.resolve().relative_to(_program())
    except ValueError:
        return
    raise RuntimeError("the program tree ships tools; an agent may manage only its own and the installed ones")




def _traced(verb: str, fn):
    """One line per management verb: what it did, and the rule when it refused."""
    def call(**kwargs):
        fields = {"channel": str(kwargs.get("channel") or "-")}
        if kwargs.get("name"):
            fields["tool_name"] = kwargs["name"]
        if kwargs.get("category"):
            fields["category"] = kwargs["category"]
        with trace.span(f"tools.{verb}", **fields) as sp:
            try:
                result = fn(**kwargs)
            except BaseException as exc:
                sp.failed(exc)
                raise
            sp.ok(ok=bool(result.get("ok", True)), enabled=result.get("enabled"),
                  registered=result.get("registered"), removed=bool(result.get("removed")))
            return result
    return call

def build(ctx):
    from tools.registry import Tool, ToolError

    def _check_name(name: str) -> str:
        name = str(name or "").strip()
        if not NAME.match(name):
            raise ToolError("a tool name is lowercase letters, digits and hyphens, 2-49 characters")
        return name

    def _config(profile: Path) -> Path:
        return profile / "config.yaml"

    def _packs(profile: Path) -> list[str]:
        cfg = _config(profile)
        if not cfg.is_file():
            return []
        for line in cfg.read_text().splitlines():
            if line.strip().startswith("packs:"):
                m = re.search(r"\[(.*?)\]", line)
                if m:
                    return [x.strip() for x in m.group(1).split(",") if x.strip()]
        return []

    def _write_packs(profile: Path, names: list[str]) -> None:
        cfg = _config(profile)
        text = cfg.read_text() if cfg.is_file() else "tools:\n"
        line = f"  packs: [{', '.join(names)}]"
        if re.search(r"^\s*packs:.*$", text, re.M):
            text = re.sub(r"^\s*packs:.*$", line.strip(), text, count=1, flags=re.M)
        else:
            text = text.rstrip() + "\n" + line + "\n"
        from core import durable
        durable.write_state(cfg, text)

    def tools_list(limit: int = 100) -> dict:
        from tools import loader
        profile, _ = _roots(ctx)
        rows = loader.discover(profile_root=profile)
        enabled = set(_packs(profile))
        return {"ok": True, "count": len(rows), "tools": [
            {"name": r["name"], "origin": r["origin"], "actions": r.get("actions", []),
             "enabled": r["name"] in enabled or r["origin"] == "program",
             "problem": r.get("problem")} for r in rows[: int(limit)]]}

    def tools_read(name: str = "") -> dict:
        from tools import loader
        profile, _ = _roots(ctx)
        row = next((r for r in loader.discover(profile_root=profile) if r["name"] == name), None)
        if not row:
            raise ToolError(f"no tool named {name!r}")
        folder = Path(row["dir"])
        script = next(iter(sorted((folder / "scripts").glob("*.py"))), None)
        return {"ok": True, "name": name, "origin": row["origin"], "actions": row.get("actions", []),
                "descriptor": (folder / "TOOL.md").read_text()[:6000],
                "script": script.name if script else None,
                "script_head": script.read_text()[:2000] if script else ""}

    def tools_create(name: str = "", description: str = "", verb: str = "", code: str = "") -> dict:
        from tools import loader
        name = _check_name(name)
        profile, _ = _roots(ctx)
        folder = profile / "tools" / name
        _refuse_program(folder)
        if folder.exists():
            raise ToolError(f"{folder} already exists; remove it first or choose another name")
        if any(r["name"] == name for r in loader.discover(profile_root=profile)):
            raise ToolError(f"a tool named {name!r} is already installed; two would fight for the name")
        (folder / "scripts").mkdir(parents=True)
        verb = verb or "run"
        params = {"type": "object", "properties": {
            "action": {"type": "string", "enum": [verb]},
            "sub": {"type": "string"}},
            "required": ["action"]}
        (folder / "TOOL.md").write_text(
            f"---\nname: {name}\ndescription: {description or 'A tool created by the agent.'}\n"
            f"kind: script\nscript: scripts/{name}.py\nactions: {verb}\n"
            f"parameters: '{json.dumps(params, separators=(',', ':'))}'\ntimeout_s: 60\n---\n\n# {name}\n\n"
            f"{description or ''}\n", encoding="utf-8")
        script = folder / "scripts" / f"{name}.py"
        script.write_text(code or SCAFFOLD.format(description=description or name, verb=verb), encoding="utf-8")
        script.chmod(0o755)
        try:
            compile(script.read_text(), str(script), "exec")
        except SyntaxError as exc:
            shutil.rmtree(folder, ignore_errors=True)
            raise ToolError(f"the script you supplied does not compile: {exc}") from exc
        registered = [r["name"] for r in loader.discover(profile_root=profile) if r["name"] == name]
        packs = _packs(profile)
        if name not in packs:
            _write_packs(profile, packs + [name])
        return {"ok": True, "path": str(folder), "registered": bool(registered), "enabled": True,
                "actions": [verb]}

    def tools_remove(name: str = "", confirm: bool = False) -> dict:
        from tools import loader
        profile, _ = _roots(ctx)
        row = next((r for r in loader.discover(profile_root=profile) if r["name"] == name), None)
        if not row:
            raise ToolError(f"no tool named {name!r}")
        if row["origin"] == "program":
            raise ToolError(f"{name} ships with Athena; it can be disabled but not removed")
        if not confirm:
            raise ToolError(f"removing {name} deletes {row['dir']}; call again with confirm=true")
        folder = Path(row["dir"])
        _refuse_program(folder)
        shutil.rmtree(folder)
        packs = [p for p in _packs(profile) if p != name]
        _write_packs(profile, packs)
        return {"ok": True, "removed": str(folder),
                "still_registered": any(r["name"] == name for r in loader.discover(profile_root=profile))}

    def _toggle(name: str, on: bool) -> dict:
        from tools import loader
        profile, _ = _roots(ctx)
        if not any(r["name"] == name for r in loader.discover(profile_root=profile)):
            raise ToolError(f"no tool named {name!r} is installed")
        packs = [p for p in _packs(profile) if p != name]
        if on:
            packs.append(name)
        _write_packs(profile, packs)
        return {"ok": True, "name": name, "enabled": on, "packs": packs}

    def tools_enable(name: str = "") -> dict:
        return _toggle(name, True)

    def tools_disable(name: str = "") -> dict:
        return _toggle(name, False)

    def _p(**props):
        return {"type": "object", "properties": props, "required": []}

    return [
        Tool("list", "Every tool visible to you, with its origin, verbs and whether it is enabled.",
             _p(limit={"type": "integer"}), _traced("list", tools_list)),
        Tool("read", "One tool's descriptor and the head of its script.",
             _p(name={"type": "string"}), _traced("read", tools_read)),
        Tool("create", "Create a tool package in your own tools root and enable it.",
             _p(name={"type": "string"}, description={"type": "string"}, verb={"type": "string"},
                code={"type": "string"}), _traced("create", tools_create)),
        Tool("remove", "Delete a tool you own. Shipped tools can be disabled but not removed.",
             _p(name={"type": "string"}, confirm={"type": "boolean"}), _traced("remove", tools_remove)),
        Tool("enable", "Enable an installed tool for this agent.", _p(name={"type": "string"}), _traced("enable", tools_enable)),
        Tool("disable", "Disable a tool for this agent without removing it.",
             _p(name={"type": "string"}), _traced("disable", tools_disable)),
    ]

def run(args=None, ctx=None):
    """The entry point the loader calls: `action` selects a verb, the rest are that verb's arguments."""
    from tools.loader import dispatch

    return dispatch(build, args, ctx, "tools")
