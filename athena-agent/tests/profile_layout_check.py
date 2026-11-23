"""Every profile has somewhere to work: `sandbox/` and `workspace/`, always.

Not a nicety — the scope guard points at both, and a profile without them cannot be tested in. This checks the
three ways a profile comes into existence (fresh create, an old profile missing them, and a profile half-built by
an interrupted run) and the repair pass the server runs at boot.
"""
import sys
import tempfile
from pathlib import Path

import _boot  # noqa: F401 — puts the project root on sys.path

from core import profile
from server.channels import ChannelManager

checks = {}


class FakeCfg(dict):
    def get(self, key, default=None):
        return super().get(key, default)


with tempfile.TemporaryDirectory() as td:
    agents = Path(td) / "profiles"
    agents.mkdir(parents=True)

    # -- 1. a fresh profile -------------------------------------------------
    fresh = agents / "fresh"
    profile.ensure(fresh)
    checks["create_makes_sandbox"] = (fresh / "sandbox").is_dir()
    checks["create_makes_workspace"] = (fresh / "workspace").is_dir()
    checks["create_makes_the_rest"] = all((fresh / p).exists() for p in
                                          ("assistant/ASSISTANT.md", "memories/MEMORY.md", "sessions/compression/SUMMARY.md"))

    # -- 2. an old profile that never had them ------------------------------
    old = agents / "old"
    (old / "assistant").mkdir(parents=True)
    (old / "assistant" / "ASSISTANT.md").write_text("# old\n", encoding="utf-8")
    result = profile.ensure(old)
    checks["repair_adds_sandbox"] = (old / "sandbox").is_dir()
    checks["repair_adds_workspace"] = (old / "workspace").is_dir()

    # -- 3. idempotent: a second pass creates nothing and touches nothing ---
    before = sorted(str(p.relative_to(old)) for p in old.rglob("*"))
    again = profile.ensure(old)
    after = sorted(str(p.relative_to(old)) for p in old.rglob("*"))
    checks["second_pass_creates_nothing"] = again["created"] == []
    checks["second_pass_changes_nothing"] = before == after
    checks["existing_files_untouched"] = (old / "assistant" / "ASSISTANT.md").read_text(encoding="utf-8") == "# old\n"

    # -- 4. the server's boot repair, over every profile on disk ------------
    broken = agents / "broken"
    (broken / "memories").mkdir(parents=True)
    managers = ChannelManager(FakeCfg(), provider=None, tools_registry=None, agents_dir=agents)
    repaired = managers.repair_layout()
    checks["boot_repair_reports_the_profiles_it_fixed"] = sorted(repaired) == ["broken"]
    for name in ("fresh", "old", "broken"):
        checks[f"boot_repair_covers_{name}"] = (agents / name / "sandbox").is_dir() and \
                                               (agents / name / "workspace").is_dir()

    # -- 5. dot-directories are not profiles --------------------------------
    decoy = agents / ".cache"
    decoy.mkdir()
    managers.repair_layout()
    checks["dot_dirs_skipped"] = not (decoy / "sandbox").exists()

for name, passed in checks.items():
    print(f"  {'PASS' if passed else 'FAIL'}  {name}")
print("\nRESULT:", "PASS" if all(checks.values()) else "FAIL")
