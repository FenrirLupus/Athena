"""Athena CLI — start the multiplexer, or run a single turn without the HTTP surface.

    python -m cli serve            run the multiplexer
    python -m cli turn <channel> <text>
    python -m cli channels            list resident channels
    python -m cli config           print the effective config
    python -m cli selftest         exercise the loop offline against the mock provider
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from core.context_window import effective_window
from core import timefmt
from core.root import athena_root as athena_root_path


def pathlib_root(root):
    """`root` is the profiles directory; anything importing pathlib here would shadow names in main()."""
    from pathlib import Path
    return Path(root)

USAGE = """athena — the multiplexer

  serve                 start the HTTP surface (127.0.0.1:8765 by default)
  turn <channel> <text>    run one turn on a channel and print the result
  channels                 list resident channels
  config                print the effective config
  tools [lane]          what one turn of this agent is offered, and where each tool comes from
  skills [lane]         the skills this agent can see (shared and its own)
  plugins [lane]        plugin folders, which are enabled, and what each registered
  security [baseline|check|report]   red/blue watchdogs: re-baseline, check now, or print the report
  plugins install <staged>          install a plugin an agent staged in its sandbox (manifest required)
  plugins installed                 what the installer put there, with hashes on record
  plugins remove <name>             disable, verify, archive with a manifest
  packs [lane]          tool packs in .athena/tools, which are enabled, and the tool each provides
  profile <channel>        scaffold and print a profile's layout
  models                list models downloaded into .athena/models
  models download <repo> [file]     fetch a GGUF from Hugging Face
  models remove <name>  delete a downloaded model
  models show <name>    print where a model lives and how it will be served
  selftest              run the offline loop test against the mock provider
"""


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(USAGE)
        return 0

    cmd = argv[0]

    if cmd == "metrics":
        from core import metrics as metrics_mod
        from config import loader
        cfg = loader.load()
        # os.path.join, not Path: this function has local `from pathlib import Path` imports further down, which
        # make the name local to the whole function and raise UnboundLocalError here
        root = os.path.join(str(athena_root_path()), "profiles")
        limit = None
        channel = ""
        for i, arg in enumerate(argv):
            if arg == "--limit" and i + 1 < len(argv):
                limit = int(argv[i + 1])
            if arg == "--channel" and i + 1 < len(argv):
                channel = argv[i + 1]
        # argv[0] is "metrics"; the subcommand is the next word
        if len(argv) > 1 and argv[1] == "rollup":
            from core import metrics as metrics_mod2
            targets = [channel] if channel else sorted(
                p.name for p in pathlib_root(root).iterdir() if p.is_dir())
            results = [metrics_mod2.rollup(root, ch) for ch in targets if ch]
            for r in results:
                print(f"{r['channel']:<10} rolled {r['rolled']:>5} rows · kept {r['kept']:>4}"
                      + (f" · archived {r.get('archived_file')}" if r.get("archived_file") else ""))
            return 0
        summary = metrics_mod.summary(root, limit=limit, channel=channel)
        total = summary["total"]
        if not total["turns"]:
            print(f"no turns recorded yet ({summary['log']})")
            return 0
        print(f"metrics · {summary['log']}")
        print(f"{total['turns']} turns · {total['total_tokens']:,} tokens · "
              f"${total['cost_usd']:.4f} · {total['errors']} errors")
        header = f"{'scope':<26}{'turns':>6}{'tokens':>10}{'cost $':>9}{'p50 s':>8}{'p95 s':>8}{'tok/s':>8}"
        print(header)
        print("-" * len(header))
        def line(label, item):
            print(f"{label:<26}{item['turns']:>6}{item['total_tokens']:>10,}"
                  f"{item['cost_usd']:>9.4f}{str(item['seconds_p50'] or '—'):>8}"
                  f"{str(item['seconds_p95'] or '—'):>8}{str(item['tokens_per_second_pooled'] or '—'):>8}")
        line("ALL", total)
        for name, item in summary["channels"].items():
            line(f"  channel {name}", item)
        for name, item in summary["models"].items():
            line(f"  {name}", item)
        # D2: the log's own size, and a warning before it becomes a problem to rotate
        scope = channel
        logs = ([metrics_mod.path_for(root, scope)] if scope
                else [metrics_mod.path_for(root, p) for p in
                      sorted(x.name for x in pathlib_root(root).iterdir() if x.is_dir())])
        total_bytes = 0
        for path in logs:
            try:
                total_bytes += path.stat().st_size
            except OSError:
                continue
        warn = "  ⚠ rotation due" if total_bytes > metrics_mod.ROLLUP_SIZE_BYTES else ""
        print(f"\nrate: ${(cfg.get('metrics') or {}).get('usd_per_mtok', 1.0)} per 1M tokens"
              f" · window {summary['first']} → {summary['last']}")
        print(f"log: {total_bytes / 1024:.0f} KiB across {len(logs)} profile(s){warn}"
              f" · rollup threshold {metrics_mod.ROLLUP_SIZE_BYTES // 1024} KiB"
              f" · folded so far: {summary['archived']['periods']} period(s)")
        return 0

    if cmd == "serve":
        from config import loader
        import uvicorn

        cfg = loader.load()
        srv = cfg.get("server") or {}
        host = str(srv.get("host") or "127.0.0.1")
        port = int(srv.get("port") or 8765)
        reload = "--reload" in argv
        for warning in cfg.get("_warnings") or []:
            print(f"config warning: {warning}", file=sys.stderr)
        print(f"athena 0.0.1 · provider={(cfg.get('model') or {}).get('provider')} "
              f"· window={effective_window(cfg)} · http://{host}:{port}"
              + (" · reload on change" if reload else ""))
        if reload:
            # an import string, not an app object: the reloader runs the app in a child so it can swap it
            # without the parent dying — which is what saves a kill/relaunch cycle per code change
            uvicorn.run("server.app:create_app", factory=True, host=host, port=port,
                        log_level="info", reload=True,
                        reload_dirs=[__import__("os").path.dirname(
                            __import__("os").path.dirname(__import__("os").path.abspath(__file__)))])
        else:
            from server.app import create_app
            uvicorn.run(create_app(), host=host, port=port, log_level="info")
        return 0

    if cmd == "turn":
        if len(argv) < 3:
            print("usage: turn <channel> <text>", file=sys.stderr)
            return 2
        from config import loader
        from providers import base as provider_base
        from server.channels import ChannelManager
        from tools import registry as tools_registry

        cfg = loader.load()
        managers = ChannelManager(cfg, provider_base.build(cfg),
                              tools_registry.default_registry(cfg),
                              loader.paths(cfg)["agents"])
        result = managers.submit(argv[1], " ".join(argv[2:]))
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0

    if cmd == "channels":
        from config import loader
        agents = loader.paths(loader.load())["agents"]
        if not agents.exists():
            print("no channels yet")
            return 0
        for channel in sorted(agents.iterdir()):
            if channel.is_dir():
                sessions = channel / "sessions"
                files = list(sessions.glob("*.jsonl")) if sessions.exists() else []
                print(f"{channel.name:<24} sessions={len(files)}")
        return 0

    if cmd == "profile":
        if len(argv) < 2:
            print("usage: profile <channel>  — scaffold and print the profile layout", file=sys.stderr)
            return 2
        from config import loader
        from core import profile as profile_mod
        agents = loader.paths(loader.load())["agents"]
        root = agents / argv[1]
        root.mkdir(parents=True, exist_ok=True)
        info = profile_mod.ensure(root)
        print(json.dumps(info, indent=2))
        for path in sorted(root.rglob("*")):
            if path.is_file():
                print(f"  {path.relative_to(root)}  ({path.stat().st_size} bytes)")
        return 0

    if cmd == "models":
        from models import store as model_store

        action = argv[1] if len(argv) > 1 else "list"
        if action == "list":
            rows = model_store.list_models()
            if not rows:
                print(f"no models in {model_store.models_dir()} — run: athena models download <repo>")
                return 0
            for r in rows:
                size = f"{(r['bytes'] or 0) / 1e9:.2f} GB" if r.get("bytes") else "?"
                print(f"{r['name']:<28} {r['quant'] or '-':<8} {size:<9} {r['repo']}")
            return 0
        if action == "download":
            if len(argv) < 3:
                print("usage: models download <repo> [file]", file=sys.stderr)
                return 2
            repo = argv[2]
            file = argv[3] if len(argv) > 3 else ""
            print(f"downloading {repo}{'/' + file if file else ''} into {model_store.models_dir()}")
            meta = model_store.download(repo, file)
            print(json.dumps(meta, indent=2))
            return 0
        if action == "remove":
            if len(argv) < 3:
                print("usage: models remove <name>", file=sys.stderr)
                return 2
            print(f"removed {model_store.remove(argv[2])}")
            return 0
        if action == "show":
            if len(argv) < 3:
                print("usage: models show <name>", file=sys.stderr)
                return 2
            from config import loader
            from providers.local import LocalServer
            cfg = loader.load()
            cfg.setdefault("model", {}).setdefault("local", {})["name"] = argv[2]
            cfg["model"]["local"]["enabled"] = True
            server = LocalServer(cfg)
            print(json.dumps({"model": str(server.model_path()),
                              "served_as": server.served_name(),
                              "endpoint": server.endpoint(),
                              "command": server.render()}, indent=2))
            return 0
        print(f"unknown models action: {action}", file=sys.stderr)
        return 2

    if cmd == "config":
        from config import loader

        if argv[1:2] == ["init"] or argv[1:2] == ["update"]:
            # write every setting with its effective value: the file is the whole surface an operator can tune
            written = loader.write_full(loader.load())
            print(f"wrote {written} — every setting, with its current value")
            return 0
        print(json.dumps(loader.load(), indent=2, default=str))
        return 0

    if cmd in ("tools", "skills", "plugins", "packs"):
        from config import loader
        from core import plugins as plugins_mod
        from core import skills as skills_mod
        from tools import filesystem, registry as registry_mod

        cfg = loader.load()
        paths = loader.paths(cfg)
        agents = paths["agents"]
        athena_root = agents.parent
        # which profile: the argument, else `default`
        lane = next((a for a in argv[1:] if not a.startswith("-")), "default")
        profile_root = agents / lane

        if cmd == "skills":
            found = skills_mod.discover(profile_root)
            print(f"skills for {lane} ({len(found)}):")
            for skill in found:
                origin = "profile" if skill["origin"] == "profile" else "shared "
                print(f"  {origin} {skill['name']:<22} {skill['description'][:70]}")
            if not found:
                print(f"  none — add {athena_root}/skills/<name>/SKILL.md")
            return 0

        if cmd == "packs":
            from core import toolpacks as packs_mod

            enabled = packs_mod.enabled_names(cfg)
            found = packs_mod.discover(profile_root, athena_root)
            print(f"tools ({len(found)} found, {len(enabled)} enabled):")
            for pack in found:
                state = "enabled" if pack["name"] in enabled else "off    "
                mark = "" if pack["has_descriptor"] else "  (no TOOL.md)"
                scripts = ", ".join(pack["scripts"]) or "no scripts/"
                refs = pack.get("references") or []
                extra = f"  +{len(refs)} refs" if refs else ""
                print(f"  {state} {pack['origin']:<8} {pack['name']:<16} {mark}  {scripts}{extra}")
            for name in enabled:
                if not any(f["name"] == name for f in found):
                    print(f"  MISSING  {name} — enabled in config but no such folder")
            return 0

        if cmd == "plugins" and (argv[1:2] or [""])[0] in ("install", "remove", "uninstall", "installed"):
            from core import plugininstall

            plugins_root = athena_root / "plugins"
            action = argv[1]
            rest = [a for a in argv[2:] if not a.startswith("-")]
            flags = [a for a in argv[2:] if a.startswith("-")]

            if action == "installed":
                rows = plugininstall.installed(plugins_root, athena_root)
                if not rows:
                    print("nothing installed through the installer (see ./athena.sh plugins for what exists)")
                    return 0
                print(f"installed ({len(rows)}):")
                for row in rows:
                    state = "present" if row["present"] else "MISSING"
                    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(row["installed_at"] or 0))
                    print(f"  {row['name']:<20} {state:<8} {row['files']:>3} files  {when}  {row['source'][:60]}")
                return 0

            if not rest:
                print(f"usage: ./athena.sh plugins {action} <name-or-path>", file=sys.stderr)
                return 2

            try:
                if action == "install":
                    staged = Path(rest[0])
                    if not staged.is_absolute():
                        # allow a bare name that lives in any profile's staging folder
                        candidates = [athena_root / "profiles" / lane / "sandbox" / "staging" / rest[0]
                                      for lane in sorted(x.name for x in (athena_root / "profiles").iterdir()
                                                         if x.is_dir())]
                        staged = next((c for c in candidates if c.is_dir()), staged)
                    result = plugininstall.install(staged, plugins_root, athena_root,
                                                   force="--force" in flags)
                    print(f"installed {result['name']} → {result['path']}")
                    print(f"  files: {result['files']} · enabled: {result['enabled']}")
                    env = result.get("environment") or {}
                    if env.get("venv"):
                        print(f"  venv: {len(env.get('packages') or [])} package(s) — "
                              f"{', '.join((env.get('packages') or [])[:6])}")
                        if env.get("shared_stack"):
                            print(f"  shares the server stack read-only: {env['shared_stack']}")
                        if env.get("from_lock"):
                            print("  installed from requirements.lock (reproducible)")
                        elif env.get("lock_written"):
                            print("  requirements.lock written from what resolved — repairs will reproduce it")
                    if env.get("error"):
                        print(f"  deps failed: {env['error']}")
                    smoke = result.get("smoke") or {}
                    if smoke:
                        print(f"  smoke: loaded={smoke['loaded']} tools={smoke['tools']} "
                              f"isolation={smoke.get('isolation')} {smoke.get('error','')}")
                    print("  next: ./athena.sh security baseline   (record the addition as authorised)")
                    return 0 if result["enabled"] != "rolled back — the plugin did not load" else 1

                if action in ("remove", "uninstall"):
                    result = plugininstall.uninstall(rest[0], plugins_root, athena_root)
                    print(f"removed {result['name']}: enabled={result['enabled']} "
                          f"archived={result.get('archived_at') or '(nothing on disk)'}")
                    if result.get("note"):
                        print(f"  {result['note']}")
                    return 0
            except Exception as exc:                  # noqa: BLE001 — a CLI reports, it does not traceback
                print(f"install error: {type(exc).__name__}: {exc}", file=sys.stderr)
                return 1

        if cmd == "plugins":
            enabled = plugins_mod.enabled_names(cfg)
            found = plugins_mod.discover(profile_root, athena_root)
            print(f"plugins ({len(found)} found, {len(enabled)} enabled):")
            for plugin in found:
                state = "enabled" if plugin["name"] in enabled else "off    "
                entry = "" if plugin["has_entry"] else "  (no plugin.py)"
                extra = []
                if plugin.get("references"):
                    extra.append(f"{len(plugin['references'])} refs")
                if plugin.get("assets"):
                    extra.append(f"{len(plugin['assets'])} assets")
                tail = ("  " + ", ".join(extra)) if extra else ""
                print(f"  {state} {plugin['origin']:<8} {plugin['name']}{entry}{tail}")
            for name in enabled:
                if not any(p["name"] == name for p in found):
                    print(f"  MISSING  {name} — enabled in config but no such folder")
            report = plugins_mod.load_all(cfg, registry_mod.Registry(), profile_root, athena_root)
            for row in report["loaded"]:
                print(f"  loaded   {row['name']} → tools: {', '.join(row['tools']) or '(none)'}")
            for row in report["skipped"]:
                print(f"  FAILED   {row['name']}: {row['error']}")
            return 0

        # tools: what one turn of this agent would actually be offered
        ctx = filesystem.Context(scope=None, approvals=None)
        ctx.cfg, ctx.profile_root, ctx.athena_root = cfg, profile_root, athena_root
        reg = registry_mod.default_registry(cfg).bind(ctx)
        schemas = reg.schemas()
        names = sorted(t["function"]["name"] for t in schemas)
        plugins_loaded = getattr(reg, "plugin_report", {}).get("loaded", [])
        plugin_tools = {t for row in plugins_loaded for t in row["tools"]}
        packs_loaded = getattr(reg, "pack_report", {}).get("loaded", [])
        pack_tools = {row["tool"] for row in packs_loaded if row.get("kind") != "builtin"}
        system_tools = {name for row in packs_loaded if row.get("kind") == "builtin"
                        for name in row.get("tools", [])}
        print(f"tools offered to {lane} ({len(names)}):")
        for name in names:
            source = "system" if name in system_tools else \
                     "toolpack" if name in pack_tools else \
                     "plugin" if name in plugin_tools else \
                     "fs" if name in {"ls", "read", "write", "append", "mkdir", "move", "copy", "delete",
                                      "stat", "find"} else \
                     "skill" if name.startswith("skill_") else "builtin"
            print(f"  {source:<8} {name}")
        for row in getattr(reg, "plugin_report", {}).get("skipped", []):
            print(f"  FAILED   plugin {row['name']}: {row['error']}")
        for row in getattr(reg, "pack_report", {}).get("skipped", []):
            print(f"  FAILED   pack {row['name']}: {row['error']}")
        for name in getattr(reg, "pack_report", {}).get("missing", []):
            print(f"  MISSING  pack {name} — named in tools.packs but no tool.yaml")
        return 0

    if cmd == "security":
        from config import loader
        from core import security

        cfg = loader.load()
        athena_root = loader.paths(cfg)["agents"].parent
        folder = athena_root / ((cfg.get("security") or {}).get("folder") or "security")
        ledger = security.Ledger(folder)
        baseline = security.Baseline(athena_root, folder)
        action = argv[1] if len(argv) > 1 else "check"
        if action not in ("baseline", "report", "check", "ledger"):
            # falls through to `check` only for a known verb; an unknown one is refused, so a mistyped subcommand cannot
            # print a clean baseline and exit 0
            print(f"unknown security action: {action}\n  use: baseline | check | report | ledger", file=sys.stderr)
            return 2

        if action == "ledger":
            from core import identity as identity_mod

            args = argv[2:]
            limit, since, kind, agent, lane = 20, 3600.0, "", "", ""
            index = 0
            while index < len(args):
                flag, value = args[index], (args[index + 1] if index + 1 < len(args) else "")
                if flag == "--limit":
                    limit = int(value or 20)
                    index += 2
                elif flag == "--since":
                    since = float(value or 3600)
                    index += 2
                elif flag == "--kind":
                    kind = value
                    index += 2
                elif flag == "--agent":
                    agent = value
                    index += 2
                elif flag == "--lane":
                    lane = value
                    index += 2
                else:
                    index += 1
            rows = ledger.recent(seconds=since)
            if kind:
                rows = [r for r in rows if r.get("kind") == kind]
            if agent:
                try:
                    identity_mod.resolve(loader.paths(cfg)["agents"], agent)
                except identity_mod.IdentityError as exc:
                    print(str(exc), file=sys.stderr)
                    return 2
                wanted = identity_mod.ids(loader.paths(cfg)["agents"], agent)
                rows = [r for r in rows if r.get("agent_id") in wanted or r.get("lane") in wanted]
            if lane:
                wanted = identity_mod.labels(loader.paths(cfg)["agents"], lane)
                rows = [r for r in rows if r.get("lane") in wanted]
            if not rows:
                print("no ledger rows match")
                return 0
            for row in rows[-limit:]:
                stamp = timefmt.stamp_from(float(row.get("at") or 0))
                who = row.get("agent_id") or row.get("lane") or ""
                print(f"{stamp}  {row.get('team',''):<4} {row.get('kind',''):<26} {who:<40} {row.get('detail','')[:80]}")
            return 0

        if action == "baseline":
            built = baseline.build()
            print(f"baseline written: {built['files']} files → {built['path']}")
            print("  this is the 'known good' state; any later difference is a finding")
            return 0

        if action == "report":
            findings = security.check(athena_root, folder, ledger, baseline)
            text = security.report(findings, baseline.load(), len(ledger.recent()))
            (folder / security.REPORT_NAME).write_text(text, encoding="utf-8")
            print(text)
            return 0

        findings = security.check(athena_root, folder, ledger, baseline)
        red = [f for f in findings if f["team"] == "red"]
        blue = [f for f in findings if f["team"] == "blue"]
        meta = baseline.load()
        print(f"baseline: {len(meta.get('files') or {})} files · ledger events (1h): {len(ledger.recent())}")
        if not findings:
            print("  clean — nothing differs")
            return 0
        print(f"  red team (inside):  {len(red)}")
        for f in red[:10]:
            print(f"    {f['severity']:<8} {f['kind']:<11} {f['path']}")
        print(f"  blue team (outside): {len(blue)}")
        for f in blue[:10]:
            print(f"    {f['severity']:<8} {f['kind']:<11} {f['path']}")
        print("\n  full report: ./athena.sh security report")
        return 0

    if cmd == "identity":
        from config import loader
        from core import identity, permissions as perms_mod

        cfg = loader.load()
        profiles = loader.paths(cfg)["agents"]
        athena_root = profiles.parent
        args = argv[1:]

        def lane_dirs():
            try:
                return [d for d in sorted(profiles.iterdir()) if d.is_dir() and not d.name.startswith(".")]
            except OSError:
                return []

        if "--resolve" in args:
            at = args.index("--resolve")
            ref = args[at + 1] if len(args) > at + 1 else ""
            try:
                row = identity.resolve(profiles, ref)
            except identity.IdentityError as exc:
                print(str(exc), file=sys.stderr)
                return 2
            print(f"{ref} → {row['label']}  {row['root']}")
            return 0

        if "--adopt" in args:
            at = args.index("--adopt")
            named = [a for a in args[at + 1:] if not a.startswith("-")]
            if named:
                root = profiles / named[0]
                if not root.is_dir():
                    print(f"no such lane: {named[0]}", file=sys.stderr)
                    return 2
                targets = [root]
            else:
                targets = []
                for root in lane_dirs():
                    try:
                        if identity.read(root) is not None:
                            continue                      # a valid id is never rewritten
                    except identity.IdentityInvalid:
                        pass                              # unparsable: adopting replaces it
                    targets.append(root)
            adopted = 0
            for root in targets:
                record = identity.mint(root, minted_by="adopt")
                adopted += 1
                print(f"adopted {root.name}: {record['id']}")
            result = perms_mod.stamp_permission_rows(athena_root, profiles)
            print(f"adopted {adopted} lane(s) · permission rows stamped: {result['stamped']}"
                  + (f" · unresolved: {result['unresolved']}" if result["unresolved"] else ""))
            return 0

        if "--add-alias" in args or "--drop-alias" in args:
            flag = "--add-alias" if "--add-alias" in args else "--drop-alias"
            at = args.index(flag)
            pair = [a for a in args[at + 1:] if not a.startswith("-")]
            if len(pair) < 2:
                print(f"{flag} needs a lane ref and an alias", file=sys.stderr)
                return 2
            try:
                if flag == "--add-alias":
                    record = identity.add_alias(profiles, pair[0], pair[1])
                else:
                    record = identity.drop_alias(profiles, pair[0], pair[1])
            except identity.IdentityError as exc:
                print(str(exc), file=sys.stderr)
                return 2
            print(f"{record['label']}: aliases = {', '.join(record['aliases']) or '(none)'}")
            return 0

        if "--break-duplicate" in args:
            at = args.index("--break-duplicate")
            name = args[at + 1] if len(args) > at + 1 else ""
            root = profiles / name
            if not name or not root.is_dir():
                print(f"no such lane: {name}", file=sys.stderr)
                return 2
            try:
                shared = identity.require(root)["id"]
            except identity.IdentityError as exc:
                print(str(exc), file=sys.stderr)
                return 2
            holders = [d.name for d in lane_dirs()
                       if d != root and _same_id(d, shared)]
            if not holders:
                print(f"{name} does not share its id with another lane — nothing to break", file=sys.stderr)
                return 2
            record = identity.remint(root, split_from=shared)
            print(f"{name} now has {record['id']} (was shared with {', '.join(holders)})")
            return 0

        # no flag: list, and say which lanes need adopting
        problems = 0
        for root in lane_dirs():
            try:
                record = identity.read(root)
            except identity.IdentityInvalid as exc:
                print(f"{root.name:<20} unusable identity — {exc}")
                problems += 1
                continue
            if record is None:
                print(f"{root.name:<20} (no identity.json — run: athena.sh identity --adopt {root.name})")
                problems += 1
                continue
            aliases = ",".join(record.get("aliases") or [])
            print(f"{root.name:<20} {record['id']}  {record.get('created','')}  {record.get('minted_by',''):<14} {aliases}")
        for shared, labels in identity.load(profiles)["duplicates"]:
            print(f"duplicate id {shared}: {', '.join(labels)} — run: athena.sh identity --break-duplicate <label>")
            problems += 1
        return 1 if problems else 0

    if cmd == "version":
        from core import version as version_mod

        args = argv[1:]
        channel = ""
        if "--channel" in args:
            at = args.index("--channel")
            channel = args[at + 1] if len(args) > at + 1 else ""
        try:
            row = version_mod.check(channel or None)
        except version_mod.VersionError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        print(f"athena {row['version']} · channel {row['channel']}")
        print(f"  file: {version_mod.VERSION_FILE}")
        if "--compare" in args:
            at = args.index("--compare")
            other = args[at + 1] if len(args) > at + 1 else ""
            try:
                order = version_mod.compare(other)
            except version_mod.VersionError as exc:
                print(str(exc), file=sys.stderr)
                return 2
            print(f"  {row['version']} vs {other}: {'older' if order < 0 else 'same' if order == 0 else 'newer'}")
            return 0 if order > 0 else 1
        return 0

    if cmd == "selftest":
        # the suite lives in tests/ so the graph shows the CLI as a surface, not as a test harness
        from tests.selftest import run as run_selftest

        return run_selftest()

    print(f"unknown command: {cmd}\n\n{USAGE}", file=sys.stderr)
    return 2


def _same_id(root, wanted: str) -> bool:
    from core import identity

    try:
        return identity.require(root)["id"] == wanted
    except identity.IdentityError:
        return False


if __name__ == "__main__":
    raise SystemExit(main())
