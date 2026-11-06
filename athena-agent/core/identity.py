"""Identity — a lane's id is a minted record, never a computed value.

The first design derived the id from the label (`uuid5(NAMESPACE, label)`). Three reviewers killed it for the same
reason: a derived id cannot tell a re-created lane from the original, so `remove("scout")` followed by
`create("scout")` hands the dead lane's grants, metric buckets and ledger rows to a brand-new agent. An id is now
**minted once** and read back; a missing file is an error that names the fix, not a cue to recompute one.

Two argument kinds, and the split is the whole API:

* file-level (`read`, `require`, `mint`, `remint`, `id_for`) take a **profile root** — one lane;
* resolution (`resolve`, `root_for`, `labels`, `ids`, `load`, `new_label_problem`) take the **profiles directory** —
  `~/.athena/profiles` — because a question about one lane needs to see every lane to answer it.

Nothing here imports another `core` module: this file is the bottom of the stack, which is what lets the scope guard,
the permission store and the CLI all use it without a cycle.

Errors are typed so the HTTP layer maps them once instead of every route deciding:
`UnknownAgent` (404), and a `Conflict` family (409) whose message always names the operator's next command.
"""
from __future__ import annotations

import json
import re
import threading
import uuid
from pathlib import Path
from core import timefmt
from core import durable

IDENTITY = "identity.json"
LABEL_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
ID_PREFIX = "id:"
MINTED_BY = ("create", "adopt", "break-duplicate")


# -- errors --------------------------------------------------------------------
class IdentityError(Exception):
    """Base for everything this module raises."""


class UnknownAgent(IdentityError, KeyError):
    """The ref names nothing. 404.

    A KeyError on purpose: every existing handler on these paths already catches `(KeyError, ValueError)`, and an
    exception that changes class silently stops being caught — which turns a 404 into a 500 on a worker thread.
    KeyError is also a LookupError, so `except LookupError` still works. The message is not repr-quoted.
    """

    def __str__(self) -> str:
        return str(self.args[0]) if self.args else "unknown lane"


class Conflict(IdentityError, ValueError):
    """The request cannot be honoured in the current state. 409, with the fix in the message.

    A ValueError for the same reason `UnknownAgent` is a KeyError: `POST /agents` already maps ValueError to 400, so
    the existing path keeps answering while the new handler can be more specific.
    """


class IdentityExists(Conflict):
    """A directory that already has a valid identity.json was asked to mint again."""


class IdentityDuplicate(Conflict):
    """Two lanes carry one id — a copied directory. Not an ambiguity to resolve: an error to repair."""


class IdentityMissing(Conflict):
    """No identity.json. Mint one; an id that was never written cannot be recovered."""


class IdentityInvalid(Conflict):
    """identity.json exists but cannot be used (not JSON, no id, id is not a UUID)."""


# -- the file ------------------------------------------------------------------
def _path(root) -> Path:
    return Path(root) / IDENTITY


def _now() -> str:
    """ISO-8601 UTC with a Z — the same format metrics writes for `ts`."""
    return timefmt.stamp()


def _valid(record) -> bool:
    if not isinstance(record, dict):
        return False
    try:
        uuid.UUID(str(record.get("id")))
    except (ValueError, AttributeError, TypeError):
        return False
    return True


def read(root):
    """The record, or None when the file is absent. IdentityInvalid when it exists but is unusable."""
    path = _path(root)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise IdentityInvalid(f"{path} cannot be read ({exc.strerror or exc})") from exc
    try:
        record = json.loads(text)
    except json.JSONDecodeError as exc:
        raise IdentityInvalid(f"{path} is not valid JSON ({exc.msg}) — mint one with "
                              f"`./athena-agent/athena.sh identity --adopt {Path(root).name}`") from exc
    if not _valid(record):
        raise IdentityInvalid(f"{path} has no usable id — mint one with "
                              f"`./athena-agent/athena.sh identity --adopt {Path(root).name}`")
    if not isinstance(record.get("aliases"), list):
        record["aliases"] = []
    return record


def require(root) -> dict:
    """read(), or raise IdentityMissing naming the command that fixes it."""
    record = read(root)
    if record is None:
        root = Path(root)
        raise IdentityMissing(
            f"{root / IDENTITY} is missing — this lane has no id.\n"
            f"Mint one with `./athena-agent/athena.sh identity --adopt {root.name}`.\n"
            f"That mints a NEW id; an id that was never written cannot be recovered.")
    return record


def id_for(root) -> str:
    """The lane's id. No fallback branch — a missing file is an error, not a derived value."""
    return require(root)["id"]


def _write(root, record: dict) -> Path:
    """tmp + fsync + os.replace: a half-written identity is indistinguishable from a corrupt one."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    path = root / IDENTITY
    durable.write_state(path, json.dumps(record, indent=1) + "\n")
    durable.fsync_dir(root)                 # the directory entry too, or the rename can be lost on a crash
    return path


def mint(root, *, minted_by: str = "create") -> dict:
    """Write a fresh identity into a directory that has none. Refuses to overwrite."""
    if minted_by not in MINTED_BY:
        raise ValueError(f"minted_by must be one of {', '.join(MINTED_BY)}")
    try:
        existing = read(root)
    except IdentityInvalid:
        # Adopt is the repair verb: an unparsable file is precisely what it exists to replace. Every other mint
        # still refuses, because overwriting a valid id is how attribution is lost.
        if minted_by != "adopt":
            raise
        existing = None
    if existing is not None:
        raise IdentityExists(f"{Path(root) / IDENTITY} already exists — refusing to overwrite an id")
    record = {"id": str(uuid.uuid4()), "created": _now(), "minted_by": minted_by, "aliases": []}
    _write(root, record)
    return record


def remint(root, *, split_from: str = "") -> dict:
    """The single exception to "never rewrite": a new id for a lane that shares one with a copy.

    `created` is re-stamped because this lane is, from now on, a different agent; `split_from` records the id it was
    separated from so the operation is legible afterwards.
    """
    require(root)                          # requires an existing, valid file
    # Aliases are cleared, not copied. The original keeps them; a copy that kept them too would make every one of
    # them resolve to two lanes, which is the ambiguity this verb exists to end.
    record = {"id": str(uuid.uuid4()), "created": _now(), "minted_by": "break-duplicate", "aliases": []}
    if split_from:
        record["split_from"] = split_from
    _write(root, record)
    return record


# -- resolution ----------------------------------------------------------------
def _entries(profiles) -> list[dict]:
    """Every lane directory with a record, plus the directories that cannot be read.

    A lane with no identity.json is *excluded* here rather than raising: `resolve` by label must still work for
    every other lane, and `/agents` has to be able to list the directory that needs `--adopt`.
    """
    profiles = Path(profiles)
    out: list[dict] = []
    try:
        children = sorted(profiles.iterdir())
    except OSError:
        return out
    for entry in children:
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        try:
            record = read(entry)
        except IdentityInvalid:
            continue
        if record is None:
            continue
        out.append({"label": entry.name, "root": entry, "id": record["id"],
                    "aliases": [str(a) for a in record.get("aliases") or []],
                    "created": record.get("created", ""), "minted_by": record.get("minted_by", "")})
    return out


def load(profiles) -> dict:
    """{"agents": {id: {...}}, "duplicates": [(id, [labels])]}.

    Nested rather than "a dict of ids plus a `duplicates` key": an id is a string, so a flat dict with a reserved key
    invites a caller iterating ids to meet a non-uuid one.
    """
    agents: dict[str, dict] = {}
    by_id: dict[str, list[str]] = {}
    for entry in _entries(profiles):
        agents[entry["id"]] = {"label": entry["label"], "aliases": entry["aliases"],
                               "root": str(entry["root"]), "created": entry["created"],
                               "minted_by": entry["minted_by"]}
        by_id.setdefault(entry["id"], []).append(entry["label"])
    duplicates = [(id_, labels) for id_, labels in by_id.items() if len(labels) > 1]
    return {"agents": agents, "duplicates": duplicates}


def _candidates(profiles) -> str:
    labels = [entry["label"] for entry in _entries(profiles)]
    return ", ".join(sorted(labels)) or "none"


def resolve(profiles, ref) -> dict:
    """Label, alias, or `id:<uuid>` → the lane. Raises UnknownAgent (with candidates) or IdentityDuplicate.

    A label always resolves, because a directory name is unique on disk — which is what lets an operator name one
    half of a duplicated pair without the resolver picking a winner first.
    """
    text = str(ref or "").strip()
    if not text:
        raise UnknownAgent("a lane ref is required")
    entries = _entries(profiles)

    if text.startswith(ID_PREFIX):
        wanted = text[len(ID_PREFIX):].strip()
        matches = [entry for entry in entries if entry["id"] == wanted]
        if not matches:
            raise UnknownAgent(f"no lane has the id {wanted} — known lanes: {_candidates(profiles)}")
        if len(matches) > 1:
            raise IdentityDuplicate(
                f"id {wanted} is shared by {', '.join(sorted(m['label'] for m in matches))} — a copied directory. "
                f"Separate one with `identity --break-duplicate <label>`")
        chosen = matches[0]
        return {**_public(chosen), "root": chosen["root"]}

    # a label wins over an alias of the same spelling: the directory is the thing that exists
    by_label = [entry for entry in entries if entry["label"] == text]
    if by_label:
        chosen = by_label[0]
        return {**_public(chosen), "root": chosen["root"]}

    by_alias = [entry for entry in entries if text in entry["aliases"]]
    if len(by_alias) > 1:
        raise IdentityDuplicate(
            f"the alias {text!r} is claimed by {', '.join(sorted(e['label'] for e in by_alias))} — "
            f"repair one with `identity --break-duplicate <label>`")
    if by_alias:
        chosen = by_alias[0]
        return {**_public(chosen), "root": chosen["root"]}

    # A directory that exists without a usable identity is not "no such lane": it is a lane whose id was never
    # written, and the operator needs the command that mints one, not a list of the lanes that happen to be fine.
    directory = Path(profiles) / text
    if directory.is_dir():
        read(directory)                     # raises IdentityInvalid for a corrupt file
        raise IdentityMissing(
            f"{directory / IDENTITY} is missing — this lane has no id.\n"
            f"Mint one with `./athena-agent/athena.sh identity --adopt {text}`.\n"
            f"That mints a NEW id; an id that was never written cannot be recovered.")

    raise UnknownAgent(f"{text!r} names no lane — known lanes: {_candidates(profiles)}")


def _public(entry: dict) -> dict:
    return {"id": entry["id"], "label": entry["label"], "aliases": list(entry["aliases"]),
            "created": entry["created"], "minted_by": entry["minted_by"]}


def root_for(profiles, ref) -> Path:
    """The lane's directory. Never creates anything — a miss must not leave a directory behind."""
    return resolve(profiles, ref)["root"]


def labels(profiles, ref) -> frozenset[str]:
    """{label} ∪ aliases — the strings a `channel` column may hold for this lane."""
    record = resolve(profiles, ref)
    return frozenset([record["label"], *record["aliases"]])


def ids(profiles, ref) -> frozenset[str]:
    """The ids a persisted grant row may carry for this ref.

    * a ref that resolves → its id (not its strings);
    * anything else → the literal, so a row naming nothing matches only itself. That is what makes a re-created
      label a stranger: no row written for the old id matches the new one.
    """
    try:
        return frozenset([resolve(profiles, ref)["id"]])
    except IdentityError:
        # Deliberately wider than "not found": a lane with a missing or corrupt identity must still be *matchable* by
        # the literal it was written under, or every permission question about it becomes an exception. Addressing
        # (resolve/root_for) raises; matching degrades to the string.
        text = str(ref or "").strip()
        if text.startswith(ID_PREFIX):
            text = text[len(ID_PREFIX):].strip()
        return frozenset([text])


# -- naming --------------------------------------------------------------------
def label_ok(name) -> bool:
    """The only rule a name that becomes a directory may be checked against. Rejects `:` by construction."""
    return bool(LABEL_RE.match(str(name or "")))


def ref_ok(ref) -> bool:
    """Addressing only: a label, or `id:<uuid>`. The routes use this; naming uses `label_ok`."""
    text = str(ref or "").strip()
    if text.startswith(ID_PREFIX):
        try:
            uuid.UUID(text[len(ID_PREFIX):].strip())
            return True
        except (ValueError, AttributeError, TypeError):
            return False
    return label_ok(text)


def new_label_problem(profiles, label) -> str:
    """\"\" when `label` may become a directory, else the reason. The one predicate every creation path applies."""
    text = str(label or "").strip()
    if not label_ok(text):
        return f"{text!r} is not a label: letters, digits, - and _ only, 1-64 characters"
    if text == "default":
        return "the queen lane is 'default' and is not created by this path"
    target = Path(profiles) / text
    if target.exists():
        try:
            record = read(target)
        except IdentityInvalid as exc:
            return f"{target} exists with an unusable identity ({exc}) — adopt or remove it first"
        if record is None:
            return f"{target} exists without an identity.json — adopt it with `identity --adopt {text}`"
        return f"{target} already names a lane"
    for entry in _entries(profiles):
        if text in entry["aliases"]:
            return f"{text!r} is already an alias of the lane {entry['label']!r}"
    return ""


# -- aliases -------------------------------------------------------------------
_ALIAS_LOCK = threading.Lock()      # process-local: add_alias is check-then-act, and the CLI is one process
# `aliases` is the field `resolve` reads and nothing wrote: only a rename appended to it, and renaming is a separate,
# cold-path document. So a lane could not be addressed by any name but its folder. These two functions are the writer,
# and they are the only ones — one file, one write, tmp + fsync + replace, like everything else here.
def _alias_problem(profiles, record: dict, alias: str) -> str:
    text = str(alias or "").strip()
    if not label_ok(text):
        return f"{text!r} is not a label: letters, digits, - and _ only, 1-64 characters"
    if text == record["label"]:
        return f"{text!r} is already this lane's own label"
    if text.startswith(ID_PREFIX):
        # checked before label_ok: label_ok rejects ':' anyway, which made this message unreachable
        return "an alias may not look like an id reference"
    if (Path(profiles) / text).exists():
        # `_entries` skips identity-less directories, so this is how an alias would otherwise shadow a lane that
        # has not been adopted yet — and a later `--adopt` would leave the alias permanently shadowing it
        return f"{text!r} is the name of an existing directory under profiles/"
    for entry in _entries(profiles):
        # compare ROOTS, not ids: two directories can share an id (a copy), and `continue` on the id skipped every
        # collision check against the sibling — so an alias could be minted equal to the other copy's label
        if entry["root"] == record["root"]:
            continue
        if entry["label"] == text:
            return f"{text!r} is another lane's label"
        if text in entry["aliases"]:
            return f"{text!r} is already an alias of {entry['label']!r}"
    return ""


def add_alias(profiles, ref, alias: str) -> dict:
    """Give a lane a second name. Idempotent, and refuses any name that already belongs to someone."""
    with _ALIAS_LOCK:
        return _add_alias_locked(profiles, ref, alias)


def _add_alias_locked(profiles, ref, alias: str) -> dict:
    record = resolve(profiles, ref)
    problem = _alias_problem(profiles, record, alias)
    if problem:
        raise Conflict(problem)
    text = str(alias).strip()
    current = require(record["root"])
    aliases = [name for name in (current.get("aliases") or []) if name != text] + [text]
    current["aliases"] = aliases
    _write(record["root"], current)
    return {**record, "aliases": aliases}


def drop_alias(profiles, ref, alias: str) -> dict:
    with _ALIAS_LOCK:
        return _drop_alias_locked(profiles, ref, alias)


def _drop_alias_locked(profiles, ref, alias: str) -> dict:
    record = resolve(profiles, ref)
    text = str(alias or "").strip()
    current = require(record["root"])
    aliases = [name for name in (current.get("aliases") or []) if name != text]
    if len(aliases) == len(current.get("aliases") or []):
        raise Conflict(f"{text!r} is not an alias of {record['label']!r}")
    current["aliases"] = aliases
    _write(record["root"], current)
    return {**record, "aliases": aliases}
