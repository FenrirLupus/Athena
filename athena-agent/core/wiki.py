"""Wiki mirror sync — the local 1:1 copy of the Athena wiki (Operator 08-12).

The wiki is the STABLE DOCTRINE: the known-good reference for how
Athena operates. .athena/.wiki/ is a local clone so the agents read
it OFFLINE instead of using the browser per consultation.

THE 1:1 RULE: the local mirror is ALWAYS an exact copy of the remote —
never a merge, never a pull that leaves stale or deleted pages. This
module wipes + re-clones fresh.

THE ATOMIC SWAP (the Operator's choice, 08-12): instead of deleting the
live dir then cloning, we clone to a sibling (.wiki.new), rename the
old dir aside (.wiki.old), rename the new in, then delete the old.
The .wiki path therefore ALWAYS exists (old copy until the new lands)
— no agent ever sees the doctrine folder missing.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from core.config import WIKI_DIR, WIKI_REPO


def sync_wiki(timeout: float = 120.0) -> dict:
    """Fresh 1:1 clone of the wiki with an atomic swap.

    Returns {"ok": bool, "pages": int, "detail": str}. Never leaves
    .wiki missing: on failure the previous copy stays in place.
    """
    parent = WIKI_DIR.parent
    staging = parent / ".wiki.new"
    old = parent / ".wiki.old"
    # Clean any leftover staging/old from a previous interrupted sync.
    shutil.rmtree(staging, ignore_errors=True)
    shutil.rmtree(old, ignore_errors=True)
    try:
        r = subprocess.run(
            ["git", "clone", WIKI_REPO, str(staging)],
            capture_output=True, text=True, timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001
        shutil.rmtree(staging, ignore_errors=True)
        return {"ok": False, "pages": _page_count(), "detail": str(exc)}
    if r.returncode != 0:
        shutil.rmtree(staging, ignore_errors=True)
        return {"ok": False, "pages": _page_count(),
                "detail": (r.stderr or r.stdout or "").strip()[:300]}
    # Atomic swap: old → .wiki.old, staging → .wiki, delete old.
    if WIKI_DIR.is_dir():
        try:
            WIKI_DIR.rename(old)
        except Exception:  # noqa: BLE001
            shutil.rmtree(staging, ignore_errors=True)
            return {"ok": False, "pages": _page_count(),
                    "detail": "could not set old copy aside"}
    try:
        staging.rename(WIKI_DIR)
    except Exception as exc:  # noqa: BLE001
        # Put the old copy back; the mirror must never be missing.
        if old.is_dir() and not WIKI_DIR.exists():
            try:
                old.rename(WIKI_DIR)
            except Exception:  # noqa: BLE001
                pass
        shutil.rmtree(staging, ignore_errors=True)
        return {"ok": False, "pages": _page_count(), "detail": str(exc)}
    shutil.rmtree(old, ignore_errors=True)
    return {"ok": True, "pages": _page_count(),
            "detail": f"1:1 clone at {WIKI_DIR}"}


def _page_count() -> int:
    """How many .md pages the current local mirror holds (0 if absent)."""
    if not WIKI_DIR.is_dir():
        return 0
    return len(list(WIKI_DIR.glob("*.md")))
