"""One version, one home, and a channel that cannot lie about it.

The tree carried `0.0.1` as a literal in five places. This suite exists so a future bump cannot miss one, and so the
channel rule is arithmetic rather than a promise: `0.0.x` alpha, `0.x.0` beta, `x.0.0` stable, with a release that
claims a channel its digits do not support refused by `check()`.

Run: ../.venv/bin/python3 tests/version_check.py
"""
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import version as V

checks: dict[str, bool] = {}


def note(name: str, passed) -> None:
    checks[name] = bool(passed)


def with_version(text: str):
    """Point the module at a temporary VERSION and return what it reports."""
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "VERSION"
        path.write_text(text + "\n", encoding="utf-8")
        original = V.VERSION_FILE
        V.VERSION_FILE = path
        try:
            return V.text(), V.channel(), V.check()
        finally:
            V.VERSION_FILE = original


# -- the real tree ------------------------------------------------------
note("version_file_exists", V.VERSION_FILE.is_file())
note("version_parses", bool(V.SEMVER.match(V.text())))
note("channel_is_one_of_three", V.channel() in V.CHANNELS)
note("line_names_both", V.line() == f"{V.text()} · {V.channel()}")

# -- the rule, in all three directions ---------------------------------
version, channel, _ = with_version("0.0.7")
note("0_0_x_is_alpha", channel == "alpha")
version, channel, _ = with_version("0.4.0")
note("0_x_0_is_beta", channel == "beta")
version, channel, _ = with_version("1.0.0")
note("x_0_0_is_stable", channel == "stable")
version, channel, _ = with_version("2.3.1")
note("major_wins_over_minor", channel == "stable")

# -- a release that claims the wrong channel is refused ----------------
with tempfile.TemporaryDirectory() as td:
    path = Path(td) / "VERSION"
    path.write_text("0.0.1\n", encoding="utf-8")
    original, V.VERSION_FILE = V.VERSION_FILE, path
    try:
        try:
            V.check("stable")
            note("alpha_cannot_claim_stable", False)
        except V.VersionError as exc:
            note("alpha_cannot_claim_stable", "bump the digit" in str(exc))
        try:
            V.check("alpha")
            note("alpha_can_claim_alpha", True)
        except V.VersionError:
            note("alpha_can_claim_alpha", False)
        try:
            V.check("nightly")
            note("unknown_channel_refused", False)
        except V.VersionError:
            note("unknown_channel_refused", True)
        note("compare_older_is_negative", V.compare("0.1.0") == -1)
        note("compare_newer_is_positive", V.compare("0.0.0") == 1)
        try:
            V.compare("0.1")
            note("malformed_compare_refused", False)
        except V.VersionError:
            note("malformed_compare_refused", True)
    finally:
        V.VERSION_FILE = original

# -- a missing VERSION is an error, never a default --------------------
with tempfile.TemporaryDirectory() as td:
    original, V.VERSION_FILE = V.VERSION_FILE, Path(td) / "VERSION"
    try:
        try:
            V.text()
            note("missing_version_refused", False)
        except V.VersionError:
            note("missing_version_refused", True)
    finally:
        V.VERSION_FILE = original

# -- the five literals are gone ----------------------------------------
py_hits, html_hits = [], []
for path in ROOT.rglob("*"):
    if not path.is_file() or "__pycache__" in path.parts or "tests" in path.parts:
        continue
    if path.suffix not in (".py", ".html", ".sh") or path.name == "VERSION":
        continue
    try:
        body = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        continue
    if '"0.0.1"' in body or "Athena 0.0.1" in body or "version=\"0.0.1\"" in body:
        (html_hits if path.suffix == ".html" else py_hits).append(path.name)
note("no_hardcoded_version_in_code", not py_hits)
note("no_hardcoded_version_in_pages", not html_hits)

# -- the CLI agrees, and exits honestly --------------------------------
def cli(*args):
    done = subprocess.run([sys.executable, "-m", "cli", *args], cwd=str(ROOT), capture_output=True, text=True)
    return done.returncode, (done.stdout + done.stderr)


code, out = cli("version")
note("cli_version_succeeds", code == 0 and V.text() in out)
code, out = cli("version", "--channel", "stable")
note("cli_refuses_a_wrong_channel", code == 2 and "bump the digit" in out)
code, out = cli("version", "--compare", "0.0.0")
note("cli_compare_newer_exits_zero", code == 0 and "newer" in out)

for name, passed in checks.items():
    print(f"  {'PASS' if passed else 'FAIL'}  {name}")
ok = sum(checks.values())
print(f"\n{ok}/{len(checks)} — RESULT:", "PASS" if ok == len(checks) else "FAIL")
