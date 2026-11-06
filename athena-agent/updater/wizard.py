"""The step-by-step wizard. Every step is also a flag, so the same code is the interactive path and the scripted one.

Interactive (what the operator sees):

    ./athena.sh update
      1 · where the build comes from      github:FenrirLupus/Athena@alpha
      2 · what is installed               0.0.1 · alpha   (branch alpha carries alpha)
      3 · what is available               <fetch, show version/channel/files/hash>
      4 · the change                      "alpha 0.0.1 → alpha 0.0.3", backups, what is kept
      5 · confirm                         y/N
      6 · apply                           backup → stop → swap → start → /health
      7 · result                          installed, with the rollback target named

Scripted (nothing prompts):

    ./athena.sh update --channel beta --yes
    ./athena.sh update --from /home/me/builds/athena --yes --no-restart-check

Dry runs stop after step 3 and change nothing. `--check` only reports.
"""
from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

from . import apply as apply_mod
from . import channels, source as source_mod
from core.jsonio import read_json

REPO_DEFAULT = "FenrirLupus/Athena"


class WizardError(RuntimeError):
    """A refusal stated before anything was changed."""


# -- small terminal helpers ----------------------------------------------------------------------------------------
def _say(text: str = "") -> None:
    print(text, flush=True)


def _step(number: int, total: int, title: str) -> None:
    _say(f"\n[{number}/{total}] {title}")


def _ask(prompt: str, *, default: str = "", yes: bool = False) -> str:
    if yes:
        return default
    try:
        answer = input(f"    {prompt}{f' [{default}]' if default else ''}: ").strip()
    except (EOFError, KeyboardInterrupt):
        raise WizardError("cancelled") from None
    return answer or default


def _confirm(prompt: str, *, yes: bool) -> bool:
    if yes:
        _say(f"    {prompt} — yes (--yes)")
        return True
    try:
        answer = input(f"    {prompt} [y/N]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return answer in ("y", "yes")


# -- the update ----------------------------------------------------------------------------------------------------
def run(*, athena_root: Path, channel: str = "", source_spec: str = "", branch: str = "",
        repo: str = REPO_DEFAULT, yes: bool = False, dry_run: bool = False, check_only: bool = False,
        port: int = 0, keep_staging: bool = False) -> dict:
    athena_root = Path(athena_root).expanduser().resolve()
    live = athena_root / "athena-agent"
    if not (live / "VERSION").is_file():
        raise WizardError(f"{live} does not look like an Athena tree (no VERSION) — run this from an install")
    installed = channels.read_version(live)
    installed_channel = channels.channel_of(installed)

    port = int(port or os.environ.get("ATHENA_PORT") or 8765)
    target_channel = (channel or installed_channel).strip().lower()
    if target_channel not in channels.CHANNELS:
        raise WizardError(f"unknown channel {target_channel!r}; choose {', '.join(channels.CHANNELS)}")

    if branch:
        branch_channel = channels.channel_for_branch(branch)
        if channel and branch_channel != target_channel:
            raise WizardError(f"branch {branch!r} carries {branch_channel}, but --channel says {target_channel}")
        target_channel = branch_channel
    target_branch = branch or channels.branch_for(target_channel)

    # 1 · source
    _step(1, 7, "Where the build comes from")
    if source_spec:
        spec = Path(source_spec).expanduser()
        src = source_mod.Source.path(spec) if spec.exists() else source_mod.Source.url(source_spec)
    else:
        src = source_mod.Source.github(repo)
    _say(f"    source: {src.describe(target_branch)}")
    _say(f"    repo:   {repo if src.kind == 'github' else '—'}")

    # 2 · installed
    _step(2, 7, "What is installed")
    _say(f"    {installed} · {installed_channel}   ({live})")
    if target_channel != installed_channel:
        _say(f"    channel switch: {installed_channel} → {target_channel} (branch {target_branch})")
    _say("    branches: main=stable · beta=beta · alpha=alpha")

    if check_only and src.kind != "path":
        return {"checked": True, "installed": installed, "channel": installed_channel,
                "target_channel": target_channel, "target_branch": target_branch}

    # 3 · fetch into staging and show what arrived
    staging_root = athena_root / "updater" / "staging"
    staging = staging_root / f"{target_channel}-{time.strftime('%Y%m%dT%H%M%S', time.gmtime())}"
    _step(3, 7, "What is available")
    _say(f"    fetching into {staging}")
    try:
        tree, facts = src.fetch(target_branch, staging)
    except source_mod.SourceError as exc:
        raise WizardError(str(exc)) from exc
    tree_facts = source_mod.tree_facts(tree)
    _say(f"    {tree_facts['version']} · {tree_facts['channel']} · {tree_facts['files']} files")
    _say(f"    sha256 {tree_facts['sha256'][:16]}…  ({facts.get('archive_sha256', 'no archive')[:16]}…)")

    try:
        channels.check_pair(tree_facts["version"], target_branch)
    except channels.ChannelError as exc:
        raise WizardError(f"{exc} — refused, nothing was changed") from exc

    order = channels.compare(tree_facts["version"], installed)
    _say(f"    installed is {installed}, available is {tree_facts['version']} "
         f"({'newer' if order > 0 else 'the same' if order == 0 else 'OLDER'})")
    if order < 0 and not yes:
        raise WizardError(f"{tree_facts['version']} is older than {installed} — refusing to move backwards "
                          f"on channel {target_channel}; pass --yes only if you mean it")

    if dry_run:
        shutil.rmtree(staging, ignore_errors=True)
        _say("\n    dry run: nothing was changed")
        return {"dry_run": True, "installed": installed, "available": tree_facts,
                "target_channel": target_channel, "target_branch": target_branch, "source": src.describe(target_branch)}

    # 4 · the change
    _step(4, 7, "The change")
    _say(f"    {installed_channel} {installed} → {target_channel} {tree_facts['version']}")
    _say(f"    backup: {athena_root / 'backups'} (zip + sha256, verified)")
    _say(f"    rollback: {live.name}.prev-{installed} stays beside the new tree")
    _say(f"    server: stopped, swapped, restarted, then /health must answer {tree_facts['version']}")

    # 5 · confirm
    _step(5, 7, "Confirm")
    if not _confirm(f"install {tree_facts['version']} and restart Athena on port {port}?", yes=yes):
        raise WizardError("declined — nothing was changed")

    # 6 · apply
    _step(6, 7, "Apply")
    state_dir = athena_root / "updater"
    row = {"channel": target_channel, "branch": target_branch, "from": installed, "ok": False,
           "source": src.describe(target_branch), "tree_sha256": tree_facts["sha256"]}
    try:
        result = apply_mod.install(tree, live, backups_dir=athena_root / "backups", port=port,
                                   branch=target_branch, logs_dir=athena_root / "logs")
    except apply_mod.ApplyError as exc:
        row.update({"ok": False, "error": str(exc), "rolled_back": getattr(exc, "rolled_back", False)})
        apply_mod.record(state_dir, row)
        raise WizardError(str(exc)) from exc
    except Exception as exc:                          # noqa: BLE001 — a crash must still be recorded
        row.update({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
        apply_mod.record(state_dir, row)
        raise

    # 7 · result
    _step(7, 7, "Result")
    for item in result["steps"]:
        _say(f"    {'ok' if item['ok'] else 'FAILED'}  {item['step']:<8} {item['note']}")
    _say(f"\n    installed {result['to']} on {target_branch} · previous tree: {result['previous_tree']}")
    _say(f"    roll back by hand: mv {result['previous_tree']} {live} && ./athena.sh serve")

    row.update({"ok": True, "to": result["to"], "backup": result["backup"]["zip"],
                "backup_sha256": result["backup"]["sha256"]})
    apply_mod.record(state_dir, row)

    if not keep_staging:
        shutil.rmtree(staging, ignore_errors=True)
    return {"ok": True, **result, "backups": result["backup"]}


def status(*, athena_root: Path) -> dict:
    """What channel the install is on, and what the last attempt did."""
    athena_root = Path(athena_root).expanduser().resolve()
    live = athena_root / "athena-agent"
    version = channels.read_version(live)
    state_file = athena_root / "updater" / "state.json"
    history = athena_root / "updater" / "history.jsonl"
    state = {}
    if state_file.is_file():
        state = read_json(state_file, {})
    last: dict = {}
    if history.is_file():
        try:
            last = json.loads(history.read_text(encoding="utf-8").strip().splitlines()[-1])
        except (ValueError, IndexError):
            last = {}
    return {"installed": version, "channel": channels.channel_of(version),
            "branch": channels.branch_for(channels.channel_of(version)),
            "state": state, "last": last,
            "previous_trees": sorted(p.name for p in athena_root.glob("athena-agent.prev-*"))}
