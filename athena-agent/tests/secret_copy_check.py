"""Secrets: names are visible, values are copyable, and nothing ever returns one.

The point of the module is a specific asymmetry:
  * the agent may learn *which* keys exist and put them where a program will read them
  * the agent may never read a value, so nothing it can do puts a key into a transcript
"""
import sys
import tempfile
from pathlib import Path

import _boot  # noqa: F401 — puts the project root on sys.path

from core import paths
from core import secretcopy

checks = {}

with tempfile.TemporaryDirectory() as td:
    athena = Path(td) / ".athena"
    home = athena / "profiles" / "probe"
    for d in (home / "workspace", home / "sandbox", athena / "athena-root-marker"):
        d.mkdir(parents=True)
    (athena / ".secret").write_text("DEEPSEEK_API_KEY=global-value-123\nOPENAI_API_KEY=other\n",
                                    encoding="utf-8")
    (home / ".secret").write_text("LOCAL_ONLY_KEY=local-value-456\n", encoding="utf-8")

    # -- names are visible, values are not ---------------------------------
    names = secretcopy.available(home, athena)
    checks["names_visible"] = names == ["DEEPSEEK_API_KEY", "LOCAL_ONLY_KEY", "OPENAI_API_KEY"]
    checks["no_value_in_names"] = all("value" not in n for n in names)

    # -- copy into the workspace -------------------------------------------
    result = secretcopy.copy_secret(home, athena, "DEEPSEEK_API_KEY", str(home / "workspace" / ".env"))
    written = Path(result["wrote"])
    checks["copied_to_workspace"] = written.exists() and "DEEPSEEK_API_KEY=global-value-123" in written.read_text()
    checks["result_carries_no_value"] = "global-value-123" not in str(result)
    checks["result_says_it_copied"] = "copied" in result["note"]
    checks["file_mode_0600"] = oct(written.stat().st_mode)[-3:] == "600"

    # -- a local key shadows the global one --------------------------------
    local = secretcopy.copy_secret(home, athena, "LOCAL_ONLY_KEY", str(home / "sandbox" / "creds.env"))
    checks["local_key_copied"] = "LOCAL_ONLY_KEY=local-value-456" in Path(local["wrote"]).read_text()

    # -- copying again replaces the line, not the file ---------------------
    target = home / "workspace" / ".env"
    secretcopy.copy_secret(home, athena, "OPENAI_API_KEY", str(target))
    text = target.read_text()
    checks["second_copy_appends_line"] = "OPENAI_API_KEY=other" in text and "DEEPSEEK_API_KEY" in text
    checks["one_line_each"] = text.count("OPENAI_API_KEY=") == 1

    # -- unknown name ------------------------------------------------------
    try:
        secretcopy.copy_secret(home, athena, "NOPE_KEY", str(home / "workspace" / "x.env"))
        checks["unknown_name_refused"] = False
    except ValueError as exc:
        checks["unknown_name_refused"] = "no secret named" in str(exc) and "DEEPSEEK_API_KEY" in str(exc)

    # -- destination outside the scope is refused -------------------------
    outside = Path(td) / "elsewhere"
    outside.mkdir()
    try:
        secretcopy.copy_secret(home, athena, "DEEPSEEK_API_KEY", str(outside / "leak.env"))
        checks["outside_destination_refused"] = False
    except ValueError as exc:
        checks["outside_destination_refused"] = "outside this agent's scope" in str(exc)
    checks["nothing_written_outside"] = not (outside / "leak.env").exists()

    # -- and the .secret files themselves are still unreadable by the agent -
    guard = paths.for_profile(home, athena_root=athena)
    try:
        guard.check(home / ".secret", paths.READ)
        checks["guard_still_refuses_secrets"] = False
    except ValueError:
        checks["guard_still_refuses_secrets"] = True

for name, passed in checks.items():
    print(f"  {'PASS' if passed else 'FAIL'}  {name}")
print("\nRESULT:", "PASS" if all(checks.values()) else "FAIL")
