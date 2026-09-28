"""Prompt-file guardrail test — 10% soft per section, 30% max total."""
from __future__ import annotations


def run() -> list[dict]:
    import core.config as c
    from core.identity import (section_token_budget, prompt_file_total_budget,
                               identity_over_budget, trim_identity_blocks,
                               priority_of_block, assemble_priority_blocks,
                               SECTION_SOFT_FRACTION, TOTAL_MAX_FRACTION)

    checks = []
    # 1. Soft 10% per section; max 30% total.
    checks.append({
        "name": "soft 10% / max 30%",
        "status": "ok" if SECTION_SOFT_FRACTION == 0.10
        and TOTAL_MAX_FRACTION == 0.30 else "fail",
        "detail": f"{SECTION_SOFT_FRACTION} / {TOTAL_MAX_FRACTION}",
    })
    # 2. 32768 window: section 3276 (10%), total 9830 (30%, 9830.4 → 9830).
    orig = c.load_config
    c.load_config = lambda: {"compression": {"context_window": 32768}}
    try:
        s32768 = section_token_budget()
        t32768 = prompt_file_total_budget()
    finally:
        c.load_config = orig
    checks.append({
        "name": "32768: soft 3276, max 9830",
        "status": "ok" if s32768 == 3276 and t32768 == 9830 else "fail",
        "detail": f"soft={s32768} max={t32768}",
    })
    # 3. Rounding: 9830.4 → 9830 (down), 9830.6 → 9831 (up).
    from core.identity import _round_half
    checks.append({
        "name": "rounding half-down/up",
        "status": "ok" if _round_half(9830.4) == 9830 and _round_half(9830.6) == 9831 else "fail",
        "detail": f"9830.4→{_round_half(9830.4)} 9830.6→{_round_half(9830.6)}",
    })
    # 4. Priority: MORE # = MORE important (###### = 6, # = 1).
    checks.append({
        "name": "priority: more # = more important",
        "status": "ok" if priority_of_block("---\n###### minor\n- x") == 6
        and priority_of_block("---\n# critical\n- x") == 1 else "fail",
        "detail": "###### → 6, # → 1",
    })
    # 5. Assembly: high-priority blocks win the budget.
    blocks = [
        "---\n###### minor\n- least",
        "---\n# critical\n- must have",
        "---\n### normal\n- medium",
    ]
    included = assemble_priority_blocks(blocks, total_budget=20, soft_budget=100)
    checks.append({
        "name": "high-priority blocks win",
        "status": "ok" if included and "###### minor" in included[0] else "fail",
        "detail": f"kept={[b.splitlines()[1][:12] for b in included]}",
    })
    # 6. Oversized identity still fits (block-aware trim).
    big_blocks = []
    for i in range(8):
        big_blocks.append(f"---\n# Section {i}\n" + "\n".join(
            f"- detail {j} " + " ".join(["word"] * 20) for j in range(150)))
    big = "\n".join(big_blocks)
    trimmed = trim_identity_blocks(big)
    checks.append({
        "name": "oversized identity fits budget",
        "status": "ok" if not identity_over_budget(trimmed) else "fail",
        "detail": f"{len(big.split())} → {len(trimmed.split())} words",
    })
    # 7. DYNAMIC budget: static sections take only their actual size; the
    #    rest flows to the dynamic sections (Assistant/User/History).
    from core.identity import dynamic_budget
    total = prompt_file_total_budget()
    dyn = dynamic_budget()  # auto-measures static
    static_used = total - dyn
    checks.append({
        "name": "static takes only its size",
        "status": "ok" if 0 < static_used < total and dyn > 0 else "fail",
        "detail": f"total={total} static={static_used} dynamic={dyn}",
    })
    checks.append({
        "name": "dynamic shrinks when static grows",
        "status": "ok" if dynamic_budget(static_tokens=2000) < dyn else "fail",
        "detail": f"{dyn} → {dynamic_budget(static_tokens=2000)}",
    })
    return checks
