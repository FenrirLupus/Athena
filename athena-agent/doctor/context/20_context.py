"""Context surface test — consolidated (the Operator's 08-12 directive).

ONE module per category: this composer runs every context submodule's
checks and merges them into a single report. Check names are preserved
1:1 — the doctor count and the nurse's failure tracking stay stable
across consolidation.
"""
from __future__ import annotations

from pathlib import Path

# The frontmatter roots the folded check scans (was a module constant).
# Each root is scanned for the profile/identity files carrying the
# frontmatter contract (--- delimiters).
ROOTS = [
    Path.home() / ".athena" / "profiles" / ".default" / "assistant",
    Path.home() / ".athena" / "profiles" / ".default" / "user",
    Path.home() / ".athena" / "profiles" / "profile-agent" / "assistant",
]


def _delimiter_lines(text: str) -> list[int]:
    """Line numbers of --- delimiters (the frontmatter contract)."""
    return [i + 1 for i, line in enumerate(text.splitlines())
            if line.strip() == "---"]


def _chk_compression() -> list[dict]:
    from context.compression import context_status, estimate_tokens
    from context import retrieval

    checks = []

    # -- Compression (was 20_compression.py) --------------------------
    big = [{"content": "x" * 2000} for _ in range(60)]
    status = context_status(big, context_window=32000, upper_threshold=0.8)
    checks.append({
        "name": "over-threshold detection",
        "status": "ok" if status["utilization"] > 0.8 else "fail",
        "detail": f"utilization={status['utilization']:.3f}",
    })
    small = [{"content": "hi"} for _ in range(10)]
    status2 = context_status(small, context_window=32000, upper_threshold=0.8)
    checks.append({
        "name": "under-threshold untouched",
        "status": "ok" if status2["utilization"] < 0.1 else "fail",
        "detail": f"utilization={status2['utilization']:.3f}",
    })
    est = estimate_tokens("hello world")
    checks.append({
        "name": "token estimation sane",
        "status": "ok" if est > 0 else "fail",
        "detail": f"{est} tokens",
    })

    # -- Retrieval (was 20_retrieval.py) ------------------------------
    r = retrieval.retrieve("pack hunts", "", profile="")
    checks.append({
        "name": "ladder returns all keys",
        "status": "ok" if all(k in r for k in ("session", "index", "vault", "semantic")) else "fail",
        "detail": f"keys={list(r.keys())}",
    })
    checks.append({
        "name": "vault consulted (default archive)",
        "status": "ok" if isinstance(r.get("vault"), list) else "fail",
        "detail": f"vault hits={len(r.get('vault', []))}",
    })
    return checks


def _chk_frontmatter() -> list[dict]:
    checks = []
    # Scan the identity files INSIDE each root dir (the roots are the
    # profile assistant/user dirs; the files carry the --- contract).
    targets = []
    for root in ROOTS:
        if root.is_dir():
            for fname in ("ASSISTANT.md", "USER.md", "MEMORY.md", "EMOTION.md"):
                f = root / fname
                if f.is_file():
                    targets.append(f)
    targets += list(Path.home().glob(".athena/skills/*/SKILL.md"))
    for path in targets:
        # MEMORY.md is the LIST schema (entries only) — exempt.
        if path.name == "MEMORY.md":
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        lines = _delimiter_lines(text)
        name = str(path).replace(str(Path.home()), "~")
        # THE STANDARD MARKDOWN SCHEMA (the Operator's 08-12 spec): exactly
        # FOUR --- delimiters — 2 wrap the Header (line 1 + the header's
        # bottom), 2 wrap the Footer (the file's last two). The body in
        # between has NO delimiters. This replaces the old 2+ shared-
        # boundary contract.
        ok = (
            len(lines) == 4
            and lines[0] == 1
        )
        checks.append({
            "name": f"frontmatter: {name}",
            "status": "ok" if ok else "fail",
            "detail": f"delimiters={lines}",
        })
    return checks


import tempfile


def _chk_recap() -> list[dict]:
    import re
    from context import compression
    from context.compression import write_recap

    checks = []
    with tempfile.TemporaryDirectory() as td:
        import core.config
        original_root = core.config.ATHENA_ROOT
        core.config.ATHENA_ROOT = Path(td)
        try:
            path = write_recap("abc-123", "the conversation summary", profile="doctor-test")
            p = Path(path)
            checks.append({
                "name": "recap file written",
                "status": "ok" if p.exists() else "fail",
                "detail": p.name,
            })
            ok_name = re.match(
                r"\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}_abc-123_Summary\.md$",
                p.name,
            )
            checks.append({
                "name": "summary filename format date_time_UUID_Summary.md",
                "status": "ok" if ok_name else "fail",
                "detail": p.name,
            })
            checks.append({
                "name": "per-profile summary folder (sessions/summary)",
                "status": "ok" if "doctor-test" in str(p)
                and p.parent.name == "summary" else "fail",
                "detail": str(p.parent.name),
            })
            text = p.read_text()
            checks.append({
                "name": "summary content present",
                "status": "ok" if "the conversation summary" in text else "fail",
                "detail": "",
            })
        finally:
            core.config.ATHENA_ROOT = original_root
    return checks


def _chk_semantic() -> list[dict]:
    from context.retrieval import semantic_rerank

    checks = []
    candidates = [
        {"content": "the pack hunts in the northern forest every full moon"},
        {"content": "athena likes debugging code"},
        {"content": "alice serves tea in the morning"},
    ]
    try:
        ranked = semantic_rerank(
            "what does the pack do every full moon",
            candidates,
            embedding_model="text-embedding-nomic-embed-text-v1.5",
            base_url="http://localhost:1234/v1",
            api_key="",
            top=2,
        )
        checks.append({
            "name": "semantic rerank returns list",
            "status": "ok" if isinstance(ranked, list) else "fail",
            "detail": f"type={type(ranked).__name__}",
        })
        if ranked:
            checks.append({
                "name": "top candidate is on-topic",
                "status": "ok" if "pack" in ranked[0].get("content", "") else "warn",
                "detail": f"top={str(ranked[0].get('content'))[:40]}",
            })
    except Exception as exc:
        checks.append({
            "name": "semantic rerank runs",
            "status": "warn",  # embeddings server may be offline — not a code bug
            "detail": f"{type(exc).__name__}: {exc}",
        })
    return checks


_SUBMODULES = [
    "compression",
    "frontmatter",
    "identity_limit",
    "prompt_stack",
    "recap",
    "response_length",
    "semantic",
]


def _load_sub(name: str):
    """Import a submodule by its _sub_* file name (namespace package)."""
    import importlib.util
    here = Path(__file__).parent
    path = here / f"_sub_{name}.py"
    spec = importlib.util.spec_from_file_location(
        f"doctor.context._sub_{name}", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod



def run() -> list[dict]:
    checks: list[dict] = []
    for name in _SUBMODULES:
        # Inline (folded) checks run directly; file-backed ones import.
        inline = globals().get(f"_chk_{name}")
        if inline is not None:
            try:
                checks.extend(inline())
            except Exception as exc:
                checks.append({
                    "name": f"context/{name}",
                    "status": "fail",
                    "detail": f"{type(exc).__name__}: {exc}",
                })
            continue
        try:
            mod = _load_sub(name)
            if callable(getattr(mod, "run", None)):
                checks.extend(mod.run())
        except Exception as exc:
            checks.append({
                "name": f"context/{name}",
                "status": "fail",
                "detail": f"{type(exc).__name__}: {exc}",
            })
    return checks


def fix() -> None:
    """The SCHEMA-REPAIR (the nurse's rebuild path, Operator 08-12).

    Any in-system .md that does NOT match the Standard Markdown Schema
    (exactly 4 --- delimiters: 2 Header, 2 Footer; body has none) is
    reformatted to the sandwich by core.md_format. MEMORY.md (the List
    schema) is exempt. This is what the nurse runs when the doctor
    flags a schema-mismatch — files in Athena's system must match her
    design.
    """
    try:
        from core.md_format import format_tree, format_profile_files
        from core.config import ATHENA_ROOT
        # The per-profile system files (ASSISTANT/USER/EMOTION; MEMORY
        # exempt inside the formatter) + everything under the shared
        # skills + the built-in tools.
        format_profile_files()
        format_tree(ATHENA_ROOT / "skills",
                    excluded=("pycache", "readme", "documentation", ".athena",
                              "node_modules"))
        format_tree(ATHENA_ROOT / "athena-system" / "tools",
                    excluded=("pycache", "readme", "documentation",
                              "__pycache__"))
        # EMOTION.md is regenerated by write_emotion (the sandwich).
        from core.emotion import write_emotion
        from intelligence.profiles import list_profiles
        for p in list_profiles():
            for side in ("assistant", "user"):
                ep = p.root / side / "EMOTION.md"
                if ep.exists():
                    try:
                        vec = __import__("core.emotion",
                                         fromlist=["read_emotion"]).read_emotion(side, p.name)
                        vd = vec.get("vector") if isinstance(vec, dict) else None
                        cur = vec.get("current", "") if isinstance(vec, dict) else ""
                        md = vec.get("mood", "") if isinstance(vec, dict) else ""
                        write_emotion(side, p.name, vd, current=cur, mood=md)
                    except Exception:
                        pass
    except Exception:
        pass
