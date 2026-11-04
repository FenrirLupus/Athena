"""Instruction documents: kept in the program tree, selected by trigger, attached to the turn.

Four things are checked, because each could silently be untrue:

  * the loader finds the documents and parses their triggers
  * a repository URL selects `github_install`, an uninstall phrase selects `github_uninstall`, unrelated text
    selects nothing
  * priority orders them and the cap holds
  * **the turn actually receives it** — a `run_turn` against the mock provider must carry the procedure in its
    system block, not merely compute it
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import instruction_docs
from core.budget import IterationBudget
from core.loop import run_turn
from core.session import Session
from providers.mock import MockProvider
from tools.registry import Registry

checks = {}

# -- discovery on the real tree ------------------------------------------------
docs = instruction_docs.discover()
names = [d["name"] for d in docs]
checks["finds_the_shipped_documents"] = {"github_install", "github_update", "github_uninstall"}.issubset(set(names))
# an update procedure that stops being selected is a silent failure: the agent would fall back to ad-hoc guessing
checks["update_phrase_selects_update"] = [d["name"] for d in instruction_docs.select(
    "can you update athena to the beta channel")] == ["github_update"]
checks["switch_channel_selects_update"] = [d["name"] for d in instruction_docs.select(
    "switch to alpha")] == ["github_update"]
checks["priority_orders_install_update_uninstall"] = (
    names.index("github_install") < names.index("github_update") < names.index("github_uninstall"))
checks["install_outranks_uninstall"] = names.index("github_install") < names.index("github_uninstall")
checks["triggers_parsed"] = any(
    any("github" in trigger and trigger.startswith("re:") for trigger in d["trigger"])
    for d in docs if d["name"] == "github_install")
# a URL-shaped trigger must not fire on prose that merely mentions the site
checks["url_trigger_is_precise"] = instruction_docs.select("github.com/ is a website") == []
checks["body_stripped_of_frontmatter"] = all("trigger:" not in d["body"] for d in docs)

# -- selection ----------------------------------------------------------------
install = instruction_docs.select("can we use https://github.com/google/elephant-goldfish-model ?")
checks["url_selects_install"] = [d["name"] for d in install] == ["github_install"]

dotgit = instruction_docs.select("clone this: https://gitlab.com/x/y.git")
checks["dotgit_selects_install"] = any(d["name"] == "github_install" for d in dotgit)

remove = instruction_docs.select("please uninstall the plugin that does planning")
checks["uninstall_phrase_selects_uninstall"] = [d["name"] for d in remove] == ["github_uninstall"]

checks["unrelated_text_selects_nothing"] = instruction_docs.select("what is the weather like") == []
checks["empty_text_selects_nothing"] = instruction_docs.select("") == []

both = instruction_docs.select("uninstall the old one, then install this repo from github.com/a/b")
checks["two_triggers_capped_at_two"] = len(both) == 2
checks["install_first_when_both_match"] = [d["name"] for d in both][0] == "github_install"

rendered = instruction_docs.render("look at https://github.com/a/b")
checks["render_marks_it_required"] = rendered.startswith("# Required procedure: github_install")
checks["render_carries_the_steps"] = "repo_inspect" in rendered and "never" in rendered.lower()
checks["render_is_empty_without_a_trigger"] = instruction_docs.render("just chatting") == ""

# -- a custom folder, so the loader is not secretly hard-coded -----------------
with tempfile.TemporaryDirectory() as td:
    folder = Path(td)
    (folder / "alpha.md").write_text("---\nname: alpha\ntrigger: [\"alpha\"]\npriority: 5\n---\n\n# A\nbody-a\n",
                                     encoding="utf-8")
    (folder / "beta.md").write_text("---\nname: beta\ntrigger: [\"beta\", \"gamma\"]\npriority: 9\n---\n\n# B\nbody-b\n",
                                    encoding="utf-8")
    custom = instruction_docs.select("gamma", folder)
    checks["custom_folder_works"] = [d["name"] for d in custom] == ["beta"]
    checks["priority_orders_selection"] = [d["name"] for d in instruction_docs.select("alpha beta", folder)] == \
                                          ["beta", "alpha"]
    checks["unknown_folder_is_empty"] = instruction_docs.discover(folder / "nope") == []

# -- the turn receives it -----------------------------------------------------
with tempfile.TemporaryDirectory() as td:
    root = Path(td) / "profiles" / "probe"
    for sub in ("assistant", "user", "memories", "sessions", "sessions/compression"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    cfg = {"model": {"provider": "mock", "name": "mock-1", "window": 32768},
           "tools": {"enabled": False},
           "budget": {"system_pct": 20, "assistant_pct": 5, "user_pct": 5, "summary_pct": 10,
                      "history_pct": 20, "memory_pct": 5, "output_pct": 5, "max_iterations": 2}}
    session = Session("probe", root)
    result = run_turn(channel="probe", channel_root=root, session=session, provider=MockProvider(),
                      tools_registry=Registry(), instructions="You are a test agent.",
                      user_text="here is a repo: https://github.com/a/b", cfg=cfg,
                      budget=IterationBudget(max_turns=2, max_usd=0.10, grace_calls=0))
    prompt = result.get("system_prompt") or ""
    checks["turn_reports_the_procedure"] = result.get("procedures") == ["github_install"]
    # system_prompt is only the last 3000 chars of the block; the procedure is longer than that, so assert on a
    # line near its end (step 7 points at the uninstall document) — proof the body reached the system block
    checks["turn_system_block_carries_the_steps"] = "github_uninstall.md" in prompt

    session2 = Session("probe2", Path(td) / "profiles" / "probe2")
    for sub in ("assistant", "user", "memories", "sessions", "sessions/compression"):
        (Path(td) / "profiles" / "probe2" / sub).mkdir(parents=True, exist_ok=True)
    quiet = run_turn(channel="probe2", channel_root=Path(td) / "profiles" / "probe2", session=session2,
                     provider=MockProvider(), tools_registry=Registry(), instructions="You are a test agent.",
                     user_text="hello there", cfg=cfg,
                     budget=IterationBudget(max_turns=2, max_usd=0.10, grace_calls=0))
    checks["no_procedure_without_a_trigger"] = quiet.get("procedures") == []
    checks["quiet_turn_keeps_its_identity"] = "You are a test agent" in (quiet.get("system_prompt") or "")

for name, passed in checks.items():
    print(f"  {'PASS' if passed else 'FAIL'}  {name}")
print("\nRESULT:", "PASS" if all(checks.values()) else "FAIL")
