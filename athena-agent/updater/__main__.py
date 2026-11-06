"""`python -m updater` — the CLI the launcher calls.

Exit codes are part of the contract, because a script has to be able to tell "nothing happened" from "we undid it":

    0   installed (or a dry run / a check that found nothing to do)
    1   crashed in a way the updater did not expect
    2   refused before touching anything (channel/branch mismatch, older build, no such branch, declined)
    3   applied, then rolled back because the new build did not answer /health
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import channels, wizard
from core.root import athena_root

DEFAULT_ROOT = athena_root()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="athena update", description="Update Athena from a release channel.")
    parser.add_argument("verb", nargs="?", default="update", choices=["update", "status", "channels"],
                        help="update (default), status, channels")
    parser.add_argument("--channel", choices=list(channels.CHANNELS), default="",
                        help="target channel: stable | beta | alpha (default: the one installed)")
    parser.add_argument("--branch", default="", choices=["main", "beta", "alpha"],
                        help="target branch instead of a channel (must agree with --channel if both are given)")
    parser.add_argument("--from", dest="source_spec", default="",
                        help="install from a directory or a tarball URL instead of GitHub")
    parser.add_argument("--repo", default=wizard.REPO_DEFAULT, help=f"GitHub repo (default {wizard.REPO_DEFAULT})")
    parser.add_argument("--root", default=str(DEFAULT_ROOT), help="Athena root (default $ATHENA_ROOT or ~/.athena)")
    parser.add_argument("--port", type=int, default=0, help="server port to stop and health-check (default 8765)")
    parser.add_argument("--yes", action="store_true", help="no prompts — the scripted path")
    parser.add_argument("--dry-run", action="store_true", help="fetch and report; change nothing")
    parser.add_argument("--check", action="store_true", help="report only; do not fetch when the source is GitHub")
    parser.add_argument("--keep-staging", action="store_true", help="leave the downloaded build on disk")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.root).expanduser()

    try:
        if args.verb == "channels":
            payload = {"channels": {name: {"branch": branch, "version": hint}
                                    for name, branch in channels.CHANNEL_BRANCH.items()
                                    for hint in [channels.bump_hint(name)]}}
            print(json.dumps(payload, indent=2) if args.json else
                  "\n".join(f"{name:<7} branch {branch:<5} version {channels.bump_hint(name)}"
                            for name, branch in channels.CHANNEL_BRANCH.items()))
            return 0

        if args.verb == "status":
            report = wizard.status(athena_root=root)
            if args.json:
                print(json.dumps(report, indent=2))
            else:
                print(f"installed {report['installed']} · {report['channel']} (branch {report['branch']})")
                last = report.get("last") or {}
                if last:
                    verdict = "ok" if last.get("ok") else ("rolled back" if last.get("rolled_back") else "failed")
                    print(f"last attempt {last.get('ts', '?')}: {last.get('from', '?')} → "
                          f"{last.get('to', '?')} · {verdict}")
                else:
                    print("no update has been attempted on this install")
                if report["previous_trees"]:
                    print(f"rollback trees: {', '.join(report['previous_trees'])}")
            return 0

        result = wizard.run(athena_root=root, channel=args.channel, source_spec=args.source_spec,
                            branch=args.branch, repo=args.repo, yes=args.yes, dry_run=args.dry_run,
                            check_only=args.check, port=args.port, keep_staging=args.keep_staging)
        if args.json:
            print(json.dumps(result, indent=2, default=str))
        return 0
    except wizard.WizardError as exc:
        message = str(exc)
        print(f"refused: {message}" if "refused" not in message.lower() else message, file=sys.stderr)
        return 3 if "rolled back" in message.lower() else 2
    except channels.ChannelError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("cancelled", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
