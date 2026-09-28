"""Prompt stack test — EXACTLY 5 blocks, template order, profile-aware."""
from __future__ import annotations


def run() -> list[dict]:
    from context.prompt_builder import build_prompt_stack

    checks = []
    stack = build_prompt_stack(
        channel="user",
        channel_instructions="instructions-line",
        profile_root=None,  # the default profile (no named root)
        history=[{"role": "user", "content": "hi"},
                 {"role": "assistant", "content": "hello"}],
        retrieved=None,
        skills_index=None,
    )
    checks.append({
        "name": "stack renders non-empty",
        "status": "ok" if stack else "fail",
        "detail": f"{len(stack)} chars",
    })

    # The 5-block contract: System > Assistant > History > User > Guidelines.
    blocks = [b for b in stack.split("\n\n---\n\n") if b.strip()]
    checks.append({
        "name": "exactly 5 blocks",
        "status": "ok" if len(blocks) == 5 else "fail",
        "detail": f"{len(blocks)} blocks",
    })

    # Order markers.
    def order(block, marker):
        return block.find(marker) != -1

    labels = []
    for b in blocks:
        first = b.strip().splitlines()[0] if b.strip() else ""
        labels.append(first[:30])
    # System block carries the channel instructions.
    checks.append({
        "name": "block 1 = System (instructions)",
        "status": "ok" if "instructions-line" in blocks[0] else "fail",
        "detail": labels[0] if blocks else "",
    })
    # Assistant block carries the profile identity (non-empty — the
    # identity is whatever the default profile's ASSISTANT.md holds).
    checks.append({
        "name": "block 2 = Assistant (identity)",
        "status": "ok" if len(blocks) > 1 and blocks[1].strip() else "fail",
        "detail": labels[1] if len(blocks) > 1 else "",
    })
    # History block carries the recent conversation (JSONL).
    checks.append({
        "name": "block 3 = History (JSONL)",
        "status": "ok" if "Recent conversation (JSONL)" in blocks[2] and '{"role"' in blocks[2] else "fail",
        "detail": labels[2] if len(blocks) > 2 else "",
    })
    # Guidelines block is last.
    checks.append({
        "name": "block 5 = Guidelines (last)",
        "status": "ok" if "Athena Guidelines" in blocks[-1] else "fail",
        "detail": labels[-1] if blocks else "",
    })
    return checks
