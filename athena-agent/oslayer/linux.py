"""Linux backend — the 25 filesystem/process/network wrappers.

Every operation enforces the scope boundary first (systems/safety). The
tool registry calls these through the platform dispatcher — the wrapper
names are the SAME on windows/ (see windows.py for the sibling).

Implementation notes (Linux/POSIX):
    - paths via pathlib, / separators
    - execute/terminal: /bin/sh -c
    - process/kill: ps + SIGTERM/SIGKILL
    - download/upload: urllib (stdlib, no curl dependency)
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import signal
import subprocess
import zipfile
from datetime import datetime
from pathlib import Path

from filesystem.safety import check_read, check_write, check_command, ScopeError, ATHENA_ROOT

# The terminal output bound: command results larger than this are
# truncated with a marker (the agent never needs megabytes back).
_TERM_OUT_MAX = 200_000


class LinuxBackend:
    name = "linux"

    # -- Command assembly -------------------------------------------------
    # The hands-off contract: the assistant says "read" with a path; the
    # WRAPPER assembles the real terminal command and runs it. The model
    # never sees or touches the command — it only sees the tool name +
    # inputs. Simple in, complex out.

    @staticmethod
    def quote(path: str) -> str:
        """Shell-quote a path (safety: paths may contain spaces/glob chars)."""
        import shlex
        return shlex.quote(str(path))

    @staticmethod
    def build_command(name: str, args: dict) -> str:
        """Assemble the terminal command for a wrapper (Linux/POSIX).

        NOTE (Operator 08-11 simplification): read/write/stat/execute are
        ALIASES — they resolve to the hand-written tools (read_file /
        write_file / fs_stat / terminal) and never reach this builder,
        so their branches were removed (dead code).
        """
        p = str(args.get("path", ""))
        q = LinuxBackend.quote
        if name == "append":
            return f"cat >> {q(p)}"
        if name == "replace":
            old = args.get("old", "")
            new = args.get("new", "")
            # sed -i with '#' delimiter (path-safe, avoids '/' conflicts).
            return f"sed -i 's#{old}#{new}#g' {q(p)}" if args.get("replace_all") \
                else f"sed -i 's#{old}#{new}#' {q(p)}"
        if name == "patch":
            hunks = args.get("hunks", [])
            cmds = []
            for i, hunk in enumerate(hunks or []):
                old = hunk.get("old", "")
                new = hunk.get("new", "")
                if old:
                    cmds.append(f"sed -i 's#{old}#{new}#' {q(p)}")
            return " && ".join(cmds) if cmds else f"true  # no hunks"
        if name == "list":
            return f"ls -la {q(p) if p else '.'}"
        if name == "tree":
            depth = int(args.get("max_depth", 3))
            return f"find {q(p) if p else '.'} -maxdepth {depth} | sort"
        if name == "find":
            pattern = args.get("pattern", "*")
            ftype = args.get("file_type", "")
            kind = "-type f" if ftype == "file" else ("-type d" if ftype == "dir" else "")
            return f"find {q(p) if p else '.'} -name {q(pattern)} {kind} | head -100"
        if name == "search":
            pattern = args.get("pattern", "")
            glob = args.get("file_glob", "*")
            target = q(p) if p else "."
            return f"grep -rn --include={q(glob)} {q(pattern)} {target} | head -100"
        if name == "mkdir":
            return f"mkdir -p {q(p)}"
        if name == "exists":
            return f"test -e {q(p)} && echo true || echo false"
        if name == "hash":
            algo = args.get("algo", "sha256")
            return f"{algo}sum {q(p)}"
        if name == "copy":
            return f"cp -r {q(str(args.get('src', '')))} {q(str(args.get('dst', '')))}"
        if name == "move":
            return f"mv {q(str(args.get('src', '')))} {q(str(args.get('dst', '')))}"
        if name == "rename":
            return f"mv {q(p)} {q(str(Path(p).with_name(args.get('new_name', ''))))}"
        if name == "delete":
            return f"rm -f {q(p)}"  # executor guards the sanctum first
        if name == "terminal":
            return str(args.get("command", ""))
        if name == "process":
            filt = args.get("name", "")
            return f"ps -eo pid,ppid,comm" + (f" | grep -i {filt}" if filt else "")
        if name == "kill":
            force = " -9" if args.get("force") else ""
            return f"kill{force} {int(args.get('pid', 0))}"
        if name == "download":
            return f"curl -fsSL {q(str(args.get('url', '')))} -o {q(str(args.get('dest', '')))}"
        if name == "compress":
            dest = args.get("dest", "")
            dest_cmd = f"-o {q(dest)}" if dest else "-o {q(str(Path(p)) + '.zip')}"
            return f"zip -r -q {dest_cmd} {q(p)}"
        if name == "extract":
            dest = args.get("dest", "")
            dest_cmd = f"-d {q(dest)}" if dest else f"-d {q(str(Path(p)).removesuffix('.zip'))}"
            return f"unzip -q {q(p)} {dest_cmd}"
        return f"true  # no-op for {name}"

    @staticmethod
    def run_command(command: str, stdin: str = "", timeout: float = 60.0) -> str:
        """Execute an assembled command (stdin feeds write/append content).

        Output is BOUNDED: huge command output (multi-hundred-KB listings,
        logs) is truncated with a marker instead of returned whole — the
        caller (the agent) never needs megabytes back, and an oversized
        return is what caused pipe breaks + scratch-file workarounds.
        """
        check_command(command)
        # The Operator's 08-12 HOME rule: terminal runs in the PROFILE's
        # SANDBOX by default (never the process cwd — that polluted
        # .athena roots with stray files). Agents and drones work
        # inside sandbox/ or workspace/, never the root homes.
        cwd = None
        try:
            from intelligence.profiles import default_profile
            cwd = str(default_profile().sandbox_dir)
        except Exception:
            cwd = None
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            input=stdin or None, timeout=timeout,
            cwd=cwd,
        )
        out = (result.stdout or "").strip()
        err = (result.stderr or "").strip()
        if result.returncode != 0:
            return f"exit {result.returncode}\n{err or out}"
        # BOUND the return: keep the head + a marker for the tail.
        if len(out) > _TERM_OUT_MAX:
            out = (out[:_TERM_OUT_MAX] +
                   f"\n… [truncated: {len(out)} chars total]")
        return out or "(no output)"

    # -- content ops ----------------------------------------------------
    @staticmethod
    def read(path: str) -> str:
        resolved = check_read(path)
        with open(resolved, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()

    @staticmethod
    def write(path: str, content: str) -> str:
        resolved = check_write(path)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        with open(resolved, "w", encoding="utf-8") as fh:
            fh.write(content)
        return f"wrote {len(content)} chars to {resolved}"

    @staticmethod
    def append(path: str, content: str) -> str:
        resolved = check_write(path)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        with open(resolved, "a", encoding="utf-8") as fh:
            fh.write(content)
        return f"appended {len(content)} chars to {resolved}"

    @staticmethod
    def replace(path: str, old: str, new: str, replace_all: bool = False) -> str:
        resolved = check_write(path)
        text = resolved.read_text(encoding="utf-8", errors="replace")
        if old not in text:
            return f"no match for {old[:40]!r} in {resolved}"
        count = text.count(old) if replace_all else 1
        text = text.replace(old, new) if replace_all else text.replace(old, new, 1)
        resolved.write_text(text, encoding="utf-8")
        return f"replaced {count} occurrence(s) in {resolved}"

    @staticmethod
    def patch(path: str, hunks: list[dict]) -> str:
        """Apply multiple {old, new} hunks to a file (list of edits)."""
        resolved = check_write(path)
        text = resolved.read_text(encoding="utf-8", errors="replace")
        applied = 0
        for hunk in hunks or []:
            old = hunk.get("old", "")
            new = hunk.get("new", "")
            if not old or old not in text:
                continue
            text = text.replace(old, new, 1)
            applied += 1
        resolved.write_text(text, encoding="utf-8")
        return f"applied {applied} hunks to {resolved}"

    @staticmethod
    def delete(path: str) -> str:
        from filesystem.safety import is_sanctum
        resolved = check_write(path)
        if is_sanctum(resolved):
            raise ScopeError(f"delete in sanctum refused: {resolved} (code is never deleted)")
        if resolved.is_dir():
            if any(resolved.iterdir()):
                return f"error: directory not empty: {resolved}"
            resolved.rmdir()
            return f"removed empty dir {resolved}"
        resolved.unlink()
        return f"deleted {resolved}"

    # -- file ops -------------------------------------------------------
    @staticmethod
    def copy(src: str, dst: str) -> str:
        s = check_read(src)
        d = check_write(dst)
        d.parent.mkdir(parents=True, exist_ok=True)
        if s.is_dir():
            shutil.copytree(s, d)
        else:
            shutil.copy2(s, d)
        return f"copied {s.name} → {d}"

    @staticmethod
    def move(src: str, dst: str) -> str:
        s = check_read(src)
        d = check_write(dst)
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(s), str(d))
        return f"moved {s.name} → {d}"

    @staticmethod
    def rename(path: str, new_name: str) -> str:
        resolved = check_write(path)
        new_path = resolved.with_name(new_name)
        check_write(new_path)
        resolved.rename(new_path)
        return f"renamed {resolved.name} → {new_path.name}"

    @staticmethod
    def mkdir(path: str, recursive: bool = True) -> str:
        resolved = check_write(path)
        if recursive:
            resolved.mkdir(parents=True, exist_ok=True)
        else:
            resolved.mkdir(exist_ok=True)
        return f"mkdir {resolved}"

    @staticmethod
    def exists(path: str) -> str:
        resolved = check_read(path)
        return f"true {resolved}" if resolved.exists() else f"false {resolved}"

    # -- listing --------------------------------------------------------
    @staticmethod
    def list(path: str = "") -> str:
        resolved = check_read(path) if path else ATHENA_ROOT.resolve()
        if not resolved.is_dir():
            return f"error: not a directory: {resolved}"
        lines = []
        for child in sorted(resolved.iterdir()):
            kind = "d" if child.is_dir() else "f"
            size = child.stat().st_size if child.is_file() else "-"
            lines.append(f"{kind} {size:>10} {child.name}")
        return "\n".join(lines) if lines else "(empty)"

    @staticmethod
    def tree(path: str = "", max_depth: int = 3) -> str:
        resolved = check_read(path) if path else ATHENA_ROOT.resolve()
        lines = []

        def walk(node: Path, depth: int, prefix: str = "") -> None:
            if depth > max_depth:
                return
            children = sorted([p for p in node.iterdir() if p.name not in ("__pycache__", ".git")])
            for i, child in enumerate(children):
                last = i == len(children) - 1
                connector = "└── " if last else "├── "
                lines.append(f"{prefix}{connector}{child.name}{'/' if child.is_dir() else ''}")
                if child.is_dir():
                    walk(child, depth + 1, prefix + ("    " if last else "│   "))

        lines.append(f"{resolved.name}/")
        walk(resolved, 1)
        return "\n".join(lines[:200]) if lines else "(empty)"

    @staticmethod
    def find(path: str = "", pattern: str = "*", file_type: str = "") -> str:
        """Find files by glob pattern (name match). file_type: file|dir."""
        resolved = check_read(path) if path else ATHENA_ROOT.resolve()
        hits = []
        for p in sorted(resolved.rglob(pattern)):
            if "__pycache__" in p.parts:
                continue
            if file_type == "file" and not p.is_file():
                continue
            if file_type == "dir" and not p.is_dir():
                continue
            hits.append(str(p.relative_to(ATHENA_ROOT)))
        return "\n".join(hits[:100]) if hits else "(no matches)"

    @staticmethod
    def search(pattern: str, path: str = "", file_glob: str = "*.py") -> str:
        """Search file CONTENTS by regex."""
        resolved = check_read(path) if path else ATHENA_ROOT.resolve()
        try:
            regex = re.compile(pattern)
        except re.error as exc:
            return f"error: bad pattern: {exc}"
        targets = []
        if resolved.is_file():
            targets = [resolved]
        else:
            for p in sorted(resolved.rglob(file_glob)):
                if p.is_file():
                    targets.append(p)
        hits = []
        for p in targets[:200]:
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            for lineno, line in enumerate(text.splitlines(), start=1):
                if regex.search(line):
                    hits.append(f"{p.relative_to(ATHENA_ROOT)}:{lineno}: {line.strip()[:120]}")
        return "\n".join(hits[:100]) if hits else "(no matches)"

    # -- metadata -------------------------------------------------------
    @staticmethod
    def stat(path: str) -> str:
        resolved = check_read(path)
        st = resolved.stat()
        kind = "dir" if resolved.is_dir() else "file"
        mtime = datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds")
        return f"{kind} | size={st.st_size} | modified={mtime} | {resolved}"

    @staticmethod
    def hash(path: str, algo: str = "sha256") -> str:
        resolved = check_read(path)
        h = hashlib.new(algo)
        with open(resolved, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        return f"{algo}: {h.hexdigest()}  {resolved}"

    # -- execute / process ---------------------------------------------
    @staticmethod
    def execute(command: str, timeout: float = 60.0) -> str:
        return LinuxBackend.terminal(command, timeout)

    @staticmethod
    def terminal(command: str, timeout: float = 60.0) -> str:
        check_command(command)
        # The HOME rule: run in the profile's sandbox (never the
        # process cwd — that pollutes the root homes).
        cwd = None
        try:
            from intelligence.profiles import default_profile
            cwd = str(default_profile().sandbox_dir)
        except Exception:
            cwd = None
        result = subprocess.run(command, shell=True, capture_output=True,
                                text=True, timeout=timeout, cwd=cwd)
        out = (result.stdout or "").strip()
        err = (result.stderr or "").strip()
        if result.returncode != 0:
            return f"exit {result.returncode}\n{err or out}"
        return out or "(no output)"

    @staticmethod
    def process(name: str = "") -> str:
        """List processes (optionally filtered by name)."""
        cmd = "ps -eo pid,ppid,comm" + (f" | grep -i {name}" if name else "")
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10)
        out = (result.stdout or "").strip()
        return out or "(no processes)"

    @staticmethod
    def kill(pid: int, force: bool = False) -> str:
        try:
            os.kill(int(pid), signal.SIGKILL if force else signal.SIGTERM)
            return f"signal sent to pid {pid}"
        except ProcessLookupError:
            return f"error: no process {pid}"
        except PermissionError:
            return f"error: no permission to signal pid {pid}"

    # -- network --------------------------------------------------------
    @staticmethod
    def download(url: str, dest: str, timeout: float = 30.0) -> str:
        resolved = check_write(dest)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        import urllib.request
        with urllib.request.urlopen(url, timeout=timeout) as resp, \
                open(resolved, "wb") as fh:
            fh.write(resp.read())
        return f"downloaded {url} → {resolved} ({resolved.stat().st_size} bytes)"

    @staticmethod
    def upload(path: str, url: str, timeout: float = 30.0) -> str:
        resolved = check_read(path)
        import urllib.request
        with open(resolved, "rb") as fh:
            data = fh.read()
        req = urllib.request.Request(url, data=data, method="PUT")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return f"uploaded {resolved.name} → {url} ({resp.status})"

    # -- archives -------------------------------------------------------
    @staticmethod
    def compress(path: str, dest: str = "") -> str:
        resolved = check_read(path)
        if not dest:
            dest = str(resolved) + ".zip"
        d = check_write(dest)
        d.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(str(d), "w", zipfile.ZIP_DEFLATED) as zf:
            if resolved.is_dir():
                for p in sorted(resolved.rglob("*")):
                    if p.is_file():
                        zf.write(p, str(p.relative_to(resolved)))
            else:
                zf.write(resolved, resolved.name)
        return f"compressed → {d} ({d.stat().st_size} bytes)"

    @staticmethod
    def extract(path: str, dest: str = "") -> str:
        resolved = check_read(path)
        if not dest:
            dest = str(resolved).removesuffix(".zip") or str(resolved) + "_extracted"
        d = check_write(dest)
        d.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(str(resolved)) as zf:
            zf.extractall(str(d))
        return f"extracted {resolved.name} → {d}"
