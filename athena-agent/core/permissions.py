"""Permissions — one manager for every question the architecture asks a person.

`ApprovalBroker` answered one kind of question: "may this agent touch this path". Installs asked a different one, from
a different store, through a different endpoint, rendered by a different branch of the same script. Three sources of
truth for one idea means three places to get it wrong — and the install popup was never verified end to end.

So there is one manager, shaped like `ChannelManager`: it owns the queue, the store, the ledger trail and the
kinds, and everything else registers against it.

Two ask families, because the two situations are not alike:

| family | example | the agent | answer |
|---|---|---|---|
| **blocking** | a path outside the scope | waits for it | within `timeout_s`, else denied as an absence |
| **queued** | install a staged tool pack | moves on | whenever the user gets to it; the click *is* the action |

A blocking ask that times out is a **denial stated as an absence** — "the user is not available, resume safely" — not
a refusal. A queued ask has no timeout: nobody is being kept waiting, and a stale card is answered or dropped later.

Kinds register an *executor*: the code that performs the thing after an allow. That is what makes the popup honest —
the button does not set a flag someone else reads later, it runs the install.

Deny-first everywhere: an unknown kind, an unknown action, an expired ask or an ask already answered is refused. None
of those fall back to "allow".
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path

from core import identity
from core.approvals import ALLOW, DENY, ONCE, PERMANENT, SCOPES, SESSION, Decision
from core.jsonio import read_json
from core import durable

STORE = "permissions.json"
HISTORY_KEEP = 200

# blocking kinds make the caller wait; queued kinds do not
BLOCKING = {"path"}
QUEUED = {"install"}

UNAVAILABLE = ("the user is not available at the moment (no answer in {span}). Resume the task in a safe and secure "
               "manner: do not retry {what}, do not look for another route to it, and continue with what you can do "
               "inside your own scope. Mention the blocked step in your final answer so the user can grant it later")


class PermissionError_(Exception):
    """An ask that cannot be honoured: unknown kind, expired, already answered."""


class PermissionManager:
    """The single authority for pending questions, grants and the actions behind an allow."""

    def __init__(self, athena_root, *, timeout_s: float = 300.0, ledger=None, profiles=None):
        self.root = Path(athena_root)
        # Where lanes live. Passed in by the manager (agents.dir may be configured); the default is only a guess for
        # a standalone caller such as a test.
        self.profiles = Path(profiles) if profiles else self.root / "profiles"
        self.timeout_s = float(timeout_s)
        self.store = self.root / "security" / STORE
        self.ledger = ledger
        self._lock = threading.RLock()
        self._asks: dict[str, dict] = {}
        self._events: dict[str, threading.Event] = {}
        # The answer has to outlive the row: `resolve` removes the ask from the queue, and the waiting caller still
        # needs the verdict. Reading it back from the removed row made every answered ask look like a timeout.
        self._decisions: dict[str, Decision] = {}
        self._session: dict[str, list[tuple[str, frozenset]]] = {}
        # id -> the label it was granted under. Without it, a bucket becomes unrevocable the moment the lane's
        # identity goes missing: the caller has a label, the bucket has a uuid, and nothing joins them.
        self._session_labels: dict[str, str] = {}
        self._grants: list[dict] = []          # permanent, durable
        self._answered: list[dict] = []
        self._executors: dict[str, dict] = {}  # kind -> {"run": fn, "blocking": bool}
        self._sources: dict[str, dict] = {}    # kind -> {"list": fn, "resolve": fn} for queues owned elsewhere
        # called per target on a permanent path grant, so the profile's workspaces.json keeps showing what was
        # approved. The store here remains the authority; this is the user-facing record.
        self.on_permanent = None
        # and the other direction: leaving a mirror behind would keep the folder allowed after the grant was taken
        # away, which makes `revoke` a lie
        self.on_revoke = None
        self._load()

    # -- kinds ------------------------------------------------------------
    def register(self, kind: str, run=None, *, blocking: bool = False) -> None:
        """Teach the manager a kind. `run(row, action)` executes an allow; `blocking` means the caller waits."""
        kind = str(kind or "").strip().lower()
        if not kind:
            raise ValueError("a kind needs a name")
        with self._lock:
            self._executors[kind] = {"run": run, "blocking": bool(blocking)}
        if blocking:
            BLOCKING.add(kind)
        else:
            QUEUED.add(kind)


    # -- attached sources --------------------------------------------------
    def attach(self, kind: str, lister, resolver, *, blocking: bool = False) -> None:
        """Let a subsystem that already keeps its own queue appear in this one.

        Installs are the case: `plugininstall` owns a file-backed queue that survives a restart and is what the CLI
        drives. Rewriting it to live in memory would lose that. Attaching it makes one panel, one endpoint and one
        resolve path without a second store — and the source stays the thing that decides what actions make sense.
        """
        kind = str(kind or "").strip().lower()
        if not kind or not callable(lister) or not callable(resolver):
            raise ValueError("attach needs a kind, a lister and a resolver")
        with self._lock:
            self._sources[kind] = {"list": lister, "resolve": resolver, "blocking": bool(blocking)}
        if blocking:
            BLOCKING.add(kind)
        else:
            QUEUED.add(kind)

    # -- asking ------------------------------------------------------------
    def ask(self, kind: str, agent: str, *, summary: str, detail=(), actions=(ALLOW, DENY),
            targets=(), reason: str = "", meta: dict | None = None, wait: bool | None = None,
            timeout_s: float | None = None) -> Decision | dict:
        """Put a question to the user. Blocking kinds wait for the answer; queued kinds return the row."""
        kind = str(kind or "").strip().lower()
        with self._lock:
            spec = self._executors.get(kind)
        if spec is None:
            # deny-first: an unregistered kind is not "open", it is unknown, and unknown is never allowed
            raise PermissionError_(f"no such permission kind {kind!r} — registered: "
                                   f"{', '.join(sorted(self._executors)) or 'none'}")

        wait = bool(spec["blocking"]) if wait is None else bool(wait)
        span = float(self.timeout_s if timeout_s is None else timeout_s)
        rid = uuid.uuid4().hex[:12]
        now = time.time()
        row = {
            "id": rid, "kind": kind, "agent": str(agent), "summary": str(summary),
            "detail": [str(item) for item in detail], "actions": [str(a) for a in actions],
            "targets": [str(t) for t in targets], "reason": str(reason),
            "meta": dict(meta or {}), "asked_at": now,
            "expires_at": (now + span) if wait else 0.0,
            "blocking": wait, "answered_at": None, "decision": None, "result": None,
        }
        event = threading.Event() if wait else None
        with self._lock:
            self._asks[rid] = row
            if event is not None:
                self._events[rid] = event
        self._record("asked", f"{kind}: {row['summary']}", lane=row["agent"])

        if not wait:
            return dict(row)

        answered = event.wait(timeout=span)
        with self._lock:
            self._asks.pop(rid, None)
            self._events.pop(rid, None)
            decision = self._decisions.pop(rid, None)

        if not answered or decision is None:
            self._settle(row, DENY, ONCE, note="timeout")
            return Decision(False, ONCE, UNAVAILABLE.format(span=self._span(span), what=row["summary"]))
        return decision

    def _span(self, span: float) -> str:
        return f"{span:.0f}s" if span >= 10 else f"{span:.1f}s"

    # -- answering ---------------------------------------------------------
    def resolve(self, rid: str, decision: str = ALLOW, scope: str = ONCE) -> dict:
        """Answer a pending ask. Runs the kind's executor when the answer is an allow."""
        decision = (str(decision) or "").strip().lower()
        scope = (str(scope) or ONCE).strip().lower()
        with self._lock:
            row = self._asks.get(rid)
            sources = dict(self._sources)
        if row is None:
            for kind, spec in sources.items():
                try:
                    listed = {str(item.get("id")) for item in spec["list"]()}
                except Exception:                  # noqa: BLE001 — a broken source must not block the others
                    listed = set()
                if str(rid) in listed:
                    self._record("answered", f"{kind}: {rid} → {decision}", lane="")
                    return {"id": rid, "kind": kind, "decision": decision,
                            "result": spec["resolve"](rid, decision)}
            raise PermissionError_(f"{rid} is not pending — it was answered already, or it expired")
        with self._lock:
            if row.get("blocking") and row.get("expires_at") and time.time() > row["expires_at"]:
                self._asks.pop(rid, None)
                self._events.pop(rid, None)
                row["answered_at"] = time.time()
                late = True
            else:
                late = False

        if late:
            self._settle(row, DENY, ONCE, note="late")
            raise PermissionError_(f"{rid} expired before the answer arrived — the agent has already been told the "
                                   f"user was unavailable")

        if decision not in row["actions"]:
            raise PermissionError_(f"{decision!r} is not one of this ask's actions ({', '.join(row['actions'])})")
        if scope not in SCOPES:
            raise PermissionError_(f"scope must be one of {', '.join(SCOPES)}")
        if decision == DENY:
            scope = ONCE                       # a denial is never remembered

        allowed = decision != DENY
        verdict = Decision(allowed, scope, "" if allowed else "denied by the user")

        if allowed and scope in (SESSION, PERMANENT):
            self._remember(row, scope)

        self._settle(row, decision, scope)
        with self._lock:
            self._decisions[rid] = verdict
            event = self._events.pop(rid, None)
            self._asks.pop(rid, None)
        # publish the verdict before waking the caller, or the wake can arrive first and see nothing
        if event is not None:
            event.set()

        result = None
        if allowed:
            result = self._execute(row, decision)
        out = {"id": rid, "kind": row["kind"], "agent": row["agent"], "decision": decision,
               "scope": scope, "summary": row["summary"]}
        if result is not None:
            out["result"] = result
        return out

    def _settle(self, row: dict, decision: str, scope: str, note: str = "") -> None:
        row["answered_at"] = row.get("answered_at") or time.time()
        row["decision"] = decision
        with self._lock:
                    # the detail is part of the record: a log line that says "a path" cannot be acted on
            self._answered.append({"id": row["id"], "kind": row["kind"], "agent": row["agent"],
                                   "decision": decision, "scope": scope, "summary": row["summary"],
                                   "detail": list(row.get("detail") or []), "targets": list(row.get("targets") or []),
                                   "at": row["answered_at"], **({"note": note} if note else {})})
            self._answered = self._answered[-HISTORY_KEEP:]
        self._record("answered", f"{row['kind']}: {row['summary']} → {decision}" + (f" ({note})" if note else ""),
                     lane=row["agent"])

    def _execute(self, row: dict, action: str):
        with self._lock:
            spec = dict(self._executors.get(row["kind"]) or {})
        run = spec.get("run")
        if run is None:
            return {"note": "allowed; this kind has no action of its own"}
        try:
            result = run(row, action)
        except Exception as exc:                  # noqa: BLE001 — a failed action is a message, not a crash
            self._record("failed", f"{row['kind']}: {row['summary']} → {type(exc).__name__}: {exc}", lane=row["agent"])
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        row["result"] = result
        return result

    # -- grants ------------------------------------------------------------
    def _id_key(self, ref) -> str:
        """The id a grant row may carry for this ref.

        `identity.ids()` returns the literal when the ref names no lane, which is what keeps a test with no profiles
        directory working unchanged — and what makes a row for a removed lane match only itself afterwards.
        """
        try:
            found = identity.ids(self.profiles, ref)
        except Exception:                      # noqa: BLE001 — a broken profiles dir must not break a grant lookup
            return str(ref or "")
        return sorted(found)[0] if found else str(ref or "")
    def _remember(self, row: dict, scope: str) -> None:
        targets = row.get("targets") or []
        flags = sorted({str(flag) for flag in (row.get("meta") or {}).get("flags") or []})
        if scope == PERMANENT:
            # A permanent path grant is a *folder*: the profile's workspaces.json is a list of folders, and "always
            # allow this file" is a decision nobody can act on later. Same rule as the session grant.
            if row["kind"] == "path":
                from pathlib import Path as _Path

                prefixes: list[str] = []
                for target in targets or ["."]:
                    prefix = str(_Path(str(target)))      # exact: a file is not a folder
                    if prefix not in prefixes:
                        prefixes.append(prefix)
                targets = prefixes
            entry = {"agent": row["agent"], "agent_id": self._id_key(row["agent"]),
                     "kind": row["kind"], "targets": targets, "flags": flags,
                     "granted_at": time.time()}
            if entry["agent_id"] == str(row["agent"]):
                # the lane has no identity, so this row cannot follow it across a re-creation: say so rather than
                # writing a grant that will silently stop matching the moment identities exist
                self._record("grant-unattributed", f"{row['agent']} granted {', '.join(targets)} with no id — "
                                             f"the grant matches by label until the lane is adopted",
                             lane=row["agent"])
            with self._lock:
                self._grants = [g for g in self._grants if not (g["agent"] == entry["agent"]
                                                                and g["kind"] == entry["kind"]
                                                                and g["targets"] == targets)]
                self._grants.append(entry)
            self._save()
            if row["kind"] == "path" and callable(self.on_permanent):
                for target in targets:
                    from pathlib import Path as _P

                    if not _P(target).is_dir():
                        continue                        # workspaces.json is a list of folders; a file cannot live there
                    try:
                        self.on_permanent(row["agent"], target)
                    except Exception as exc:   # noqa: BLE001 — a failed mirror must not lose the grant
                        # the grant stands (this store is the authority); say why the profile copy is missing
                        self._record("mirror-failed", f"{row['agent']} {target}: {type(exc).__name__}: {exc}",
                                     lane=row["agent"])
            return

        if row["kind"] != "path":
            return
        from pathlib import Path as _Path

        with self._lock:
            key = self._id_key(row["agent"])
            self._session_labels[key] = str(row["agent"])
            bucket = self._session.setdefault(key, [])
            for target in targets or ["."]:
                path = _Path(str(target))
                # a FILE grant covers that file; a DIRECTORY grant covers its children. Using the parent as the
                # prefix for a file meant one "allow" on `todo.md` granted write and delete over the whole folder —
                # the opposite of what core/approvals.py promises.
                prefix = str(path)
                entry = (str(prefix), frozenset(flags))
                if entry not in bucket:
                    bucket.append(entry)

    def grants(self, agent: str) -> list[tuple[str, frozenset]]:
        with self._lock:
            return list(self._session.get(self._id_key(agent), []))

    def covers(self, agent: str, path: str, flag: str) -> bool:
        """Is this path already allowed by a session grant, or by a permanent one?"""
        from pathlib import Path as _Path

        target = _Path(str(path))
        for prefix, flags in self.grants(agent):
            if flags and flag not in flags:
                continue
            try:
                target.relative_to(prefix)
                return True
            except ValueError:
                continue
        wanted = identity.ids(self.profiles, agent)
        with self._lock:
            # rows written before identities existed carry no agent_id; they match by label, and only until the
            # lane they name stops existing — which is exactly the isolation a label cannot give
            permanent = [g for g in self._grants
                         if g["kind"] == "path"
                         and ((g.get("agent_id") and g["agent_id"] in wanted)
                              or (not g.get("agent_id") and g.get("agent") == agent))]
        for grant in permanent:
            for raw in grant.get("targets") or []:
                prefix = _Path(raw)
                prefix = prefix if not prefix.suffix else prefix.parent
                flags = set(grant.get("flags") or [])
                if flags and flag not in flags:
                    continue
                try:
                    target.relative_to(prefix)
                    return True
                except ValueError:
                    continue
        return False

    def forget_session(self, agent: str) -> None:
        """Drop the session bucket for a lane, whichever form the caller has: label, alias or id."""
        with self._lock:
            key = self._id_key(agent)
            self._session.pop(key, None)
            self._session.pop(str(agent or ""), None)

    def revoke(self, agent: str | None = None, kind: str | None = None) -> dict:
        """Take a permanent grant back — from this store *and* from the profile mirror.

        Leaving the mirror behind would keep the folder allowed through the scope's own workspace list after the
        grant was withdrawn, which makes revocation a statement rather than an action.
        """
        with self._lock:
            before = list(self._grants)
            wanted = (identity.ids(self.profiles, agent) | {str(agent or "")}) if agent is not None else frozenset()
            self._grants = [g for g in before
                            if not ((agent is None
                                     or g.get("agent_id") in wanted
                                     or (not g.get("agent_id") and g.get("agent") == agent))
                                    and (kind is None or g["kind"] == kind))]
            dropped = [g for g in before if g not in self._grants and g["kind"] == "path"]
            if agent is None:
                self._session.clear()
                self._session_labels.clear()
            else:
                # session buckets are stored under the id; a revoke that pops only the caller's spelling leaves the
                # grant live. Pop the direct keys, then every bucket that was granted under this label — which is the
                # only way to reach one whose identity file has since gone missing.
                text = str(agent or "")
                for key in [self._id_key(agent), text, *[k for k, label in self._session_labels.items() if label == text]]:
                    self._session.pop(key, None)
                    self._session_labels.pop(key, None)
            removed = len(before) - len(self._grants)
        self._save()
        if callable(self.on_revoke):
            for grant in dropped:
                for target in grant.get("targets") or []:
                    try:
                        self.on_revoke(grant["agent"], target)
                    except Exception as exc:   # noqa: BLE001 — the authority grant is already gone
                        self._record("mirror-failed", f"revoke {grant['agent']} {target}: {exc}",
                                     lane=grant["agent"])
        self._record("revoked", f"agent={agent or '*'} kind={kind or '*'} removed={removed}")
        return {"revoked": removed, "agent": agent, "kind": kind, "mirrors_cleared": len(dropped)}

    def policy(self) -> dict:
        with self._lock:
            kinds = ([{"kind": kind, "blocking": bool(spec["blocking"]), "executor": spec["run"] is not None}
                      for kind, spec in sorted(self._executors.items())]
                     + [{"kind": kind, "blocking": bool(spec["blocking"]), "executor": True, "attached": True}
                        for kind, spec in sorted(self._sources.items())])
            return {"permanent": [dict(g) for g in self._grants],
                    # keyed by id, but each entry says which lane that id is: a uuid alone is unreadable to the
                    # person this payload exists for
                    "session": [{"agent_id": key,
                                 "agent": (identity.load(self.profiles)["agents"].get(key) or {}).get("label", key),
                                 "grants": [{"prefix": prefix, "flags": sorted(flags)}
                                            for prefix, flags in grants]}
                                for key, grants in self._session.items()],
                    "pending": len(self._asks), "kinds": kinds}

    # -- the ApprovalBroker interface, unchanged ---------------------------
    def request(self, agent: str, path: str, op: str, reason: str, tool: str = "",
                flags: tuple = ()) -> Decision:
        """Ask about a path. Same signature as `ApprovalBroker.request`, so `Scope.check` needs no change."""
        return self.ask("path", agent,
                        summary=f"{op} {path}",
                        # the card shows the agent and the reason as their own labelled lines; repeating them in
                        # the detail block is how a popup ends up saying the same thing twice
                        detail=[f"operation: {op}", f"path: {path}", f"tool: {tool or 'direct'}"],
                        actions=(ALLOW, DENY), targets=[str(path)], reason=reason,
                        meta={"op": op, "tool": tool, "flags": list(flags)}, wait=True)

    # -- queue views -------------------------------------------------------
    def pending(self) -> list[dict]:
        now = time.time()
        out = []
        with self._lock:
            rows = list(self._asks.values())
        for row in rows:
            if row.get("blocking") and row.get("expires_at") and now > row["expires_at"]:
                continue                            # expired: it will be refused if answered late
            out.append({key: row[key] for key in
                        ("id", "kind", "agent", "summary", "detail", "actions", "targets", "reason",
                         "asked_at", "expires_at", "blocking", "expires_in") if key in row}
                       | {"expires_in": max(0, int((row.get("expires_at") or now) - now)) if row.get("blocking") else None})
        with self._lock:
            sources = dict(self._sources)
        for kind, spec in sources.items():
            try:
                rows = spec["list"]() or []
            except Exception:                      # noqa: BLE001
                continue
            for item in rows:
                if not isinstance(item, dict):
                    continue
                # a source is a different module with its own habits: `detail` arrives as a string from the install
                # queue, and `for line of "not installed"` renders one card per character
                raw_detail = item.get("detail")
                # a source's `detail` is its state in one word ("not installed"); the reason is its own field, so
                # appending it here is how the same sentence ends up on the card twice
                state = raw_detail if isinstance(raw_detail, str) else ""
                detail = [] if isinstance(raw_detail, str) else (raw_detail if isinstance(raw_detail, list) else [])
                out.append({"id": item.get("id"), "kind": item.get("kind") or kind,
                            "agent": item.get("agent") or "you", "summary": item.get("summary")
                            or item.get("name") or kind, "detail": detail,
                            "actions": item.get("actions") or [ALLOW, DENY],
                            "targets": item.get("targets") if isinstance(item.get("targets"), list) else [],
                            "reason": item.get("reason") if isinstance(item.get("reason"), str) else "",
                            "asked_at": item.get("created_at") or item.get("asked_at") or 0,
                            "expires_at": 0, "blocking": False, "expires_in": None,
                            "source": kind, "state": state, "warning": item.get("warning") or "",
                            "packages": item.get("packages") or [], "installed": item.get("installed")})
        return sorted(out, key=lambda r: r.get("asked_at") or 0)

    def answered(self, limit: int = 20) -> list[dict]:
        with self._lock:
            return list(self._answered[-limit:])

    # -- persistence -------------------------------------------------------
    def _load(self) -> None:
        data = read_json(self.store, None)
        if data is None:
            return
        if isinstance(data.get("permanent"), list):
            self._grants = [g for g in data["permanent"] if isinstance(g, dict)]
                # A pending ask belonged to a turn whose process is gone; answering it would resume work nobody awaits.
        # nothing, so it is closed as expired rather than silently forgotten.
        for row in data.get("pending") or []:
            self._answered.append({"id": row.get("id"), "kind": row.get("kind"), "agent": row.get("agent"),
                                   "decision": "deny", "scope": ONCE, "summary": row.get("summary"),
                                   "at": row.get("asked_at"), "note": "orphaned by a restart"})

    def _save(self) -> None:
        try:
            self.store.parent.mkdir(parents=True, exist_ok=True)
            payload = {"permanent": self._grants,
                       "pending": [{"id": row["id"], "kind": row["kind"], "agent": row["agent"],
                                    "summary": row["summary"], "asked_at": row["asked_at"]}
                                   for row in self._asks.values() if row.get("blocking")]}
            durable.write_state(self.store, json.dumps(payload, indent=1))
        except OSError:
            pass

    def _record(self, kind: str, detail: str, lane: str = "") -> None:
        if self.ledger is not None:
            try:
                self.ledger.record("blue", f"permission-{kind}", detail, lane=lane)
            except Exception:                      # noqa: BLE001
                pass


def stamp_permission_rows(athena_root, profiles) -> dict:
    """Give every grant row that lacks a usable id the id of the lane it names.

    `_remember` writes the label as `agent_id` when the lane had no identity at the time, so "has an agent_id" is not
    the test — "has a *uuid*" is. Without this, adoption silently orphans every approval given before it.
    """
    path = Path(athena_root) / "security" / STORE
    data = read_json(path, None)
    if data is None:
        return {"stamped": 0, "unresolved": 0}
    stamped = unresolved = 0
    for grant in data.get("permanent") or []:
        current = str(grant.get("agent_id") or "")
        try:
            uuid.UUID(current)
            continue                                  # already attributable
        except (ValueError, AttributeError, TypeError):
            pass
        found = [value for value in identity.ids(profiles, grant.get("agent") or "")
                 if _looks_like_uuid(value)]
        if len(found) == 1:
            grant["agent_id"] = found[0]
            stamped += 1
        else:
            unresolved += 1
    if stamped:
        durable.write_state(path, json.dumps(data, indent=1))
    return {"stamped": stamped, "unresolved": unresolved}


def _looks_like_uuid(text) -> bool:
    try:
        uuid.UUID(str(text))
        return True
    except (ValueError, AttributeError, TypeError):
        return False
