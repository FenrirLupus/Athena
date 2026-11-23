"""Where a build comes from: a GitHub branch, a tarball, or a directory on this machine.

The updater must be usable **offline** (a build you made yourself) and **verifiable** (a hash you can compare), and it
must refuse a source that does not look like an Athena tree instead of installing a half-extracted one.

Three kinds of source, one interface:

    Source.github("FenrirLupus/Athena")   → https://codeload.github.com/<repo>/tar.gz/refs/heads/<branch>
    Source.url("https://…/athena-0.1.0.tar.gz")
    Source.path("/home/me/builds/athena")  → a directory already on disk

`fetch()` always returns a *staging directory* containing `athena-agent/` with a `VERSION` in it. That is the contract
the rest of the updater relies on, so a release tarball laid out differently fails here, loudly, and not halfway
through a swap.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import tarfile
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

from . import channels
from core import trace

USER_AGENT = "athena-updater/1"
LANE = "updater"
TREE_NAME = "athena-agent"


class SourceError(RuntimeError):
    """A source that cannot be reached, is not a build, or failed its own hash."""


@dataclass
class Source:
    kind: str                       # github | url | path
    location: str
    repo: str = ""

    @classmethod
    def github(cls, repo: str) -> "Source":
        return cls("github", "", repo=repo)

    @classmethod
    def url(cls, url: str) -> "Source":
        return cls("url", url)

    @classmethod
    def path(cls, path: str | Path) -> "Source":
        return cls("path", str(Path(path).expanduser().resolve()))

    # -- description ------------------------------------------------------
    def describe(self, branch: str = "") -> str:
        if self.kind == "github":
            return f"github:{self.repo}@{branch or '?'}"
        return f"{self.kind}:{self.location}"

    # -- fetching ---------------------------------------------------------
    def _download(self, url: str, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if os.path.isfile(url):                        # a path is a source too: builds made on this machine
            shutil.copyfile(url, dest)
            return dest
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=60) as response, open(dest, "wb") as out:
                shutil.copyfileobj(response, out)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise SourceError(
                    f"{url} is not there (404). A branch with no build cannot be updated from — publish "
                    f"`{TREE_NAME}/` to that branch first.") from exc
            raise SourceError(f"{url} answered {exc.code} {exc.reason}") from exc
        except urllib.error.URLError as exc:
            raise SourceError(f"cannot reach {url}: {exc.reason} — offline, or the host is blocked") from exc
        return dest

    def fetch(self, branch: str, staging: Path) -> tuple[Path, dict]:
        """Materialise the source into `staging` and return (tree, facts)."""
        staging = Path(staging)
        staging.mkdir(parents=True, exist_ok=True)
        facts: dict = {"kind": self.kind, "branch": branch, "source": self.describe(branch)}
        with trace.span("updater.fetch", channel=LANE, kind=self.kind, branch=branch,
                        source=self.describe(branch), staging=staging.name) as sp:
            if self.kind == "path":
                origin = Path(self.location) / TREE_NAME if (Path(self.location) / TREE_NAME).is_dir() else Path(self.location)
                if not origin.is_dir():
                    raise SourceError(f"{self.location} is not a directory")
                facts["origin"] = str(origin)
                sp.ok(origin=str(origin), tree=origin.name)
                return origin, facts

            if self.kind == "github":
                url = f"https://codeload.github.com/{self.repo}/tar.gz/refs/heads/{branch}"
            else:
                url = self.location
            archive = staging / ("build.tar.gz" if url.endswith(".gz") else "build.archive")
            self._download(url, archive)
            facts["archive_sha256"] = hashlib.sha256(archive.read_bytes()).hexdigest()
            facts["archive"] = str(archive)
            tree = _extract(archive, staging)
            sp.ok(archive_bytes=archive.stat().st_size, sha256=facts["archive_sha256"][:12],
                  tree=tree.name)
        return tree, facts


def _extract(archive: Path, staging: Path) -> Path:
    """Extract a tarball or zip, find the tree inside it, and refuse anything that is not one."""
    out = staging / "unpacked"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    try:
        if zipfile.is_zipfile(archive):
            with zipfile.ZipFile(archive) as bundle:
                _safe_members(bundle.namelist())
                bundle.extractall(out)
        else:
            with tarfile.open(archive) as bundle:
                _safe_members(bundle.getnames())
                bundle.extractall(out, filter="data")     # type: ignore[call-arg]
    except (tarfile.TarError, zipfile.BadZipFile) as exc:
        raise SourceError(f"{archive.name} is not a readable archive: {exc}") from exc

    candidates = [p for p in out.iterdir() if p.is_dir()]
    # a GitHub tarball is `<repo>-<branch>/athena-agent`; a hand-made one may be `athena-agent` at the root
    for candidate in candidates:
        if (candidate / TREE_NAME / "VERSION").is_file():
            return candidate / TREE_NAME
        if (candidate / "VERSION").is_file() and candidate.name == TREE_NAME:
            return candidate
    if (out / TREE_NAME / "VERSION").is_file():
        return out / TREE_NAME
    raise SourceError(f"{archive.name} has no {TREE_NAME}/VERSION in it — that is not an Athena build")


def _safe_members(names: list[str]) -> None:
    for name in names:
        target = os.path.normpath(name)
        if target.startswith("..") or os.path.isabs(target):
            raise SourceError(f"refusing an archive that writes outside its own folder ({name})")


def tree_facts(tree: Path) -> dict:
    """Version, channel, file count and hash of a candidate tree — what the wizard shows before it touches anything."""
    tree = Path(tree)
    version = channels.read_version(tree)
    files = 0
    digest = hashlib.sha256()
    for path in sorted(tree.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        files += 1
        digest.update(str(path.relative_to(tree)).encode())
        digest.update(path.read_bytes())
    return {"version": version, "channel": channels.channel_of(version), "files": files,
            "sha256": digest.hexdigest()}
