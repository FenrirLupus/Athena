"""Parity: an agent's config.yaml carries the same settings and descriptions as the global one.

The claim being tested is not "it parses" — it is that the file an operator opens to change an agent lists the
same keys, with the same explanations, as the file they open to change the whole installation. A hand-maintained
second copy drifts the moment a default is added; this test is why the agent template is generated.
"""
import re
import sys
from pathlib import Path

import _boot  # noqa: F401 — puts the project root on sys.path

import yaml

from core import agentconfig

DEFAULTS = Path(__file__).resolve().parents[1] / "config" / "defaults.yaml"
checks = {}


def blocks(text: str) -> dict:
    """{section: [(key, comment_lines)]} for top-level sections, comments attached to the key below them."""
    out, current, pending = {}, None, []
    for line in text.splitlines():
        header = re.match(r"^([a-z_][A-Za-z0-9_]*):\s*$", line)
        if header:
            current = header.group(1)
            out.setdefault(current, [])
            pending = []
            continue
        if current is None:
            continue
        stripped = line.strip()
        if stripped.startswith("#"):
            pending.append(stripped)
            continue
        key = re.match(r"^(\s*)([a-zA-Z_][A-Za-z0-9_]*):", line)
        if key:
            out[current].append((key.group(2), tuple(pending), len(key.group(1))))
            pending = []
    return out


defaults = blocks(DEFAULTS.read_text(encoding="utf-8"))
template = blocks(agentconfig.template_for_agent())

for section in agentconfig.AGENT_SECTIONS:
    want = {(k, indent) for k, _, indent in defaults.get(section, [])}
    have = {(k, indent) for k, _, indent in template.get(section, [])}
    checks[f"{section}_keys_match"] = want == have and bool(want)

    # and the same description above each key
    want_comments = {k: c for k, c, _ in defaults.get(section, []) if c}
    have_comments = {k: c for k, c, _ in template.get(section, []) if c}
    mismatched = {k: (want_comments[k], have_comments.get(k)) for k in want_comments
                  if have_comments.get(k) != want_comments[k]}
    checks[f"{section}_comments_match"] = not mismatched
    if mismatched:
        print(f"  {section} comment mismatches:", mismatched)

checks["persona_present"] = "persona" in template and "persona" not in defaults
checks["multiplexer_sections_absent"] = not ({"server", "agents"} & set(template))
checks["template_is_valid_yaml"] = isinstance(yaml.safe_load(agentconfig.template_for_agent()), dict)
checks["global_and_agent_share_a_filename"] = agentconfig.FILENAME == "config.yaml"

# --- populated, not just present -------------------------------------------------
import tempfile

from config import loader

effective = loader.load()
rendered_global = loader.render_full(effective)
global_data = yaml.safe_load(rendered_global)
for section in ("model", "budget", "tools", "server", "agents", "metrics", "compression"):
    values = {k: v for k, v in (global_data.get(section) or {}).items() if v is not None}
    expected = {k for k, _comments, indent in defaults.get(section, []) if indent <= 2}
    checks[f"global_{section}_populated"] = len(values) >= len(expected) and bool(values)

with tempfile.TemporaryDirectory() as td:
    from pathlib import Path as _P
    root = _P(td) / "agent"
    written = agentconfig.write(root, {}, base=effective)
    agent_data = yaml.safe_load(written.read_text(encoding="utf-8"))
    blank_by_design = {(section, key) for section, keys in agentconfig.BLANK_BY_DESIGN.items()
                       for key in keys}
    total = sum(len(agent_data.get(s) or {}) for s in agentconfig.AGENT_SECTIONS)
    filled = sum(1 for section in agentconfig.AGENT_SECTIONS
                 for key, value in (agent_data.get(section) or {}).items()
                 if value is not None or (section, key) in blank_by_design)
    checks["agent_file_fully_populated"] = filled == total and total > 20
    checks["blank_by_design_keys_present"] = all(
        key in (agent_data.get(section) or {}) for section, key in blank_by_design)
    print(f"  agent file: {filled}/{total} keys populated")

for name, passed in checks.items():
    print(f"  {'PASS' if passed else 'FAIL'}  {name}")
print("\nRESULT:", "PASS" if all(checks.values()) else "FAIL")
