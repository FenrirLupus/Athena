"""SQLite layer — the vault archive and per-session stores (Layer 1).

Layout (the Operator's spec):
    sessions/vault/            — the archive + its backups
        vault.db               — chronological archive of ALL conversations
        vault-backup-###.db    — rotating backups
    sessions/session-{UUID}.db — one file PER session (its message history)

The vault is the ARCHIVE (immense, query-heavy, UUID ids). Each session
file is the IMMEDIATE store (lean: the recent window for that one session).
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import time
import uuid
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

from .config import ATHENA_ROOT, load_config


# -- The column-family doctrine (the Operator's schema) -----------------------
#
# A variable GROUP is expressed as a column-name FAMILY: a shared base
# prefix (the PARENT) + one column per member (the variants). SQL cells
# are atomic, so the family is carried by NAMING, never by nesting a
# blob inside one cell.
#
#   response_length             ← the PARENT (the primary counted value)
#   response_prediction  ← the family (system operation)
#   response_adjustment    ← the family (system operation)
#
# IMPORT/EXPORT MAPPING (the Operator's spec): when a row crosses the SQL↔JSON
# boundary, the flat family columns CONVERT to one nested JSON object —
# the parent key (response) wraps the members, each tagged by its suffix
# (length / prediction / adjusted). JSONL uses the nested shape; SQL
# stores the flat shape.
#
#   SQL row:   response_length=3  prediction=64  adjusted=16
#   JSONL:     "response": {"length": 3, "prediction": 64, "adjusted": 16}
#
# Rules:
#   1. The base column = the PARENT (what matters for counting/display).
#   2. The suffix columns = system-operation variants (prediction,
#      adjusted, etc.) — declared on every row, NULL where not applicable.
#   3. Max 3 members per family (the Operator's cap).
#   4. No duplication — a family is ONE prefix; overlap across tables
#      (session + vault) is fine, duplication within a table is not.
#   5. The free-form meta column is ONLY for loose metadata (session_id,
#      channel) — never for a structured family.
#   6. COLUMN NAMES = 1-2 words, snake_case (the Operator's rule): every
#      column is response_length / response_prediction / response_adjustment
#      — never longer. A 3-word column name is a design error.
COLUMN_FAMILIES = {
    "response_length": {
        "json_key": "response",
        "members": {
            "response_length": "length",
            "response_prediction": "prediction",
            "response_adjustment": "adjusted",
        },
    },
    "name": {
        "json_key": "name",
        "members": {
            "name_first": "first",
            "name_last": "last",
            "name_nick": "nick",
        },
    },
    "tool_call": {
        "json_key": "tool_call",
        "members": {
            "tool_call": "call",
            "tool_id": "id",
        },
    },
    "skill_call": {
        "json_key": "skill_call",
        "members": {
            "skill_call": "call",
            "skill_id": "id",
        },
    },
    "reason": {
        "json_key": "reason",
        "members": {
            "reason": "chain",
            "reason_stop": "stop",
            "reason_start": "start",
            "reason_pending": "pending",
        },
    },
    "api": {
        "json_key": "api",
        "members": {
            "api_provider": "provider",
            "api_model": "model",
        },
    },
    "usage": {
        "json_key": "usage",
        "members": {
            "usage_prompt": "prompt",
            "usage_completion": "completion",
            "usage_total": "total",
        },
    },
}


def _validate_column_name(name: str) -> bool:
    """1-2 words, snake_case (the Operator's naming rule)."""
    return isinstance(name, str) and 1 <= len(name.split("_")) <= 2


def validate_families() -> list[str]:
    """Any family column violating the 1-2 word rule (a design error)."""
    bad = []
    for fam in COLUMN_FAMILIES.values():
        for col in fam.get("members", {}):
            if not _validate_column_name(col):
                bad.append(col)
    return bad


def column_family(base: str) -> dict | None:
    """The family definition (json_key + member mapping), or None."""
    return COLUMN_FAMILIES.get(base)


def row_to_json(row: dict) -> dict:
    """Convert a SQL row's flat family columns into nested JSON groups.

    The Operator's transport mapping: flat columns → one JSON object per
    family, parent key = the family's json_key, member tags = the suffix
    names. Non-family columns pass through unchanged.
    """
    out = dict(row)
    for base, fam in COLUMN_FAMILIES.items():
        members = fam["members"]
        if any(col in row for col in members):
            group = {}
            for col, tag in members.items():
                if row.get(col) is not None:
                    try:
                        group[tag] = int(row[col])
                    except (TypeError, ValueError):
                        group[tag] = row[col]
            # Pop the flat member columns FIRST (a json_key that equals a
            # member column, e.g. name / tool_call, would otherwise be
            # wiped by the pop below).
            for col in members:
                out.pop(col, None)
            if group:
                out[fam["json_key"]] = group
    return out


def json_to_row(obj: dict) -> dict:
    """Convert JSON groups back into flat family columns (the inverse).

    The Operator's import mapping: a nested JSON group (parent key → tags)
    becomes flat columns — parent key maps to the family, each tag to
    its member column. Used by import so a JSONL line lands in the
    right SQL cells.
    """
    out = dict(obj)
    for base, fam in COLUMN_FAMILIES.items():
        group = obj.get(fam["json_key"])
        if isinstance(group, dict):
            # Pop the nested group FIRST (a json_key that equals a member
            # column, e.g. name / tool_call, would otherwise be wiped by
            # the pop after we write the flat columns).
            out.pop(fam["json_key"], None)
            for col, tag in fam["members"].items():
                if tag in group:
                    out[col] = group[tag]
    return out



def _db_cfg() -> dict:
    cfg = load_config()
    return cfg.get("db", {})


def _profile_root(profile: str = "") -> Path:
    """The data root for a profile.

    the Operator's spec: the DEFAULT profile lives natively at
    profiles/.default/ (dot-prefixed like the system profiles), NOT the
    .athena/ root. Named profiles → profiles/<name>/.
    """
    profile = (profile or "").strip()
    if not profile or profile == "default":
        return ATHENA_ROOT / "profiles" / ".default"
    return ATHENA_ROOT / "profiles" / profile


def sessions_dir(profile: str = "", kind: str = "operator") -> Path:
    """The sessions root for a profile — OPERATOR vs AGENT split (the
    Operator's 08-12 spec).

    default → .athena/sessions/ ; <name> → .athena/profiles/<name>/sessions/
    kind="operator" (default) → .../sessions/        — the OPERATOR's
        conversations. NEVER swept: these are the real chats.
    kind="agent" → .../agent/sessions/               — the AGENTS' own
        sessions (nurse, kanban thoughts, subagents, janitor). Their
        own wipe-able folder — a sweep there can never touch the
        operator's conversations.
    """
    db_cfg = _db_cfg()
    if kind == "agent":
        path = _profile_root(profile) / "agent" / "sessions"
        path.mkdir(parents=True, exist_ok=True)
        return path
    path = _profile_root(profile) / db_cfg.get("dir", "sessions")
    path.mkdir(parents=True, exist_ok=True)
    return path




def vault_dir(profile: str = "", kind: str = "operator") -> Path:
    """vault/ — the archive + backups, inside the profile's sessions dir.

    kind="agent" → the AGENTS' own vault (agent/sessions/vault) — each
    session type has its own vault (the Operator's 08-12 split: operator
    conversations + vault vs agent autonomous tasks + vault; never mixed).
    """
    db_cfg = _db_cfg()
    path = sessions_dir(profile, kind=kind) / db_cfg.get("vault_dir", "vault")
    path.mkdir(parents=True, exist_ok=True)
    return path




def vault_path(profile: str = "", kind: str = "operator") -> Path:
    db_cfg = _db_cfg()
    return vault_dir(profile, kind=kind) / db_cfg.get("vault", "vault.db")


def index_path(profile: str = "") -> Path:
    """vault/index.db — the table of contents for the archive."""
    return vault_dir(profile) / "index.db"


def session_path(session_id: str, profile: str = "", kind: str = "operator") -> Path:
    """sessions/session-{UUID}.db — one file per session, per profile.

    kind="agent" → agent/sessions/session-{UUID}.db (the agents' own
    sessions — the Operator's 08-12 split).
    """
    db_cfg = _db_cfg()
    prefix = db_cfg.get("session_prefix", "session-")
    safe_id = session_id.replace("/", "_").replace("..", "_")
    return sessions_dir(profile, kind=kind) / f"{prefix}{safe_id}.db"


# -- Session LABELS (the Operator's 08-12 spec: {UUID: Label} registry) --
# The SYSTEM sees only UUIDs; the USER sees the label. The registry lives
# in the profile's sessions dir as labels.json (one map, never touched by
# the wipe — sessions/ is a profile dir that survives).
def labels_path(profile: str = "") -> Path:
    """sessions/labels.json — the {session_id: label} registry."""
    return sessions_dir(profile) / "labels.json"


def load_session_labels(profile: str = "") -> dict[str, str]:
    """The {UUID: label} map (empty when none are set)."""
    import json as _json
    try:
        p = labels_path(profile)
        if p.exists():
            return _json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def set_session_label(session_id: str, label: str, profile: str = "") -> dict:
    """Rename a session: store {UUID: label} in the registry.

    The system still addresses the session BY ITS UUID — the label is
    purely the user's side (the dropdown/sessions page show it).
    """
    import json as _json
    labels = load_session_labels(profile)
    label = str(label).strip()
    if label:
        labels[session_id] = label
    else:
        labels.pop(session_id, None)
    p = labels_path(profile)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(_json.dumps(labels, indent=2, ensure_ascii=False),
                 encoding="utf-8")
    return {"uuid": session_id, "label": label or None, "profile": profile or "default"}


def list_session_ids(profile: str = "") -> list[str]:
    """Every session file in a profile's sessions dir (by mtime, newest first).

    The profile's own directory is scanned — named profiles keep their
    sessions under profiles/<name>/sessions/.
    """
    db_cfg = _db_cfg()
    prefix = db_cfg.get("session_prefix", "session-")
    files = []
    for path in sessions_dir(profile).glob(f"{prefix}*.db"):
        files.append((path.stat().st_mtime, path.stem[len(prefix):]))
    files.sort(reverse=True)
    return [sid for _mtime, sid in files]


def uuid_session_ids(profile: str = "", limit: int = 0) -> list[str]:
    """The profile's sessions, UUID-named ONLY (the Operator's spec).

    TEST DEBRIS / INTERNAL sessions (roles, toolcols, nurse-*, …) never
    appear in the UI — the dropdown lists only real UUID sessions that
    exist within the selected profile.
    """
    import re as _re
    _uuid = _re.compile(
        r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
        _re.I)
    ids = [sid for sid in list_session_ids(profile=profile)
           if _uuid.match(sid)]
    return ids[:limit] if limit else ids


def find_session_profile(session_id: str, start_profile: str = "") -> str:
    """THE 08-17 SESSION-OWNER RESOLUTION (the Operator's doctrine): find
    which PROFILE owns a session file. Profiles are individual — a session
    may live under any profile's sessions/ dir, and reading it must use
    that profile, never the loop's default.

    Returns the owning profile name ("" = default/.default) or "" if the
    session isn't found anywhere.
    """
    session_id = (session_id or "").strip()
    if not session_id:
        return ""
    db_cfg = _db_cfg()
    prefix = db_cfg.get("session_prefix", "session-")
    # The active-profile preference first (fast path).
    _start = (start_profile or "").strip()
    roots_to_try = []
    if _start and _start not in ("default", ".default"):
        roots_to_try.append(_start)
    roots_to_try.append("")
    try:
        from intelligence.profiles import list_profiles
        roots_to_try += [p.name for p in list_profiles()
                         if p.name and p.name not in roots_to_try]
    except Exception:
        pass
    for prof in roots_to_try:
        try:
            _sp = sessions_dir(prof or "") if prof else sessions_dir("")
            if (_sp / f"{prefix}{session_id}.db").exists():
                return prof
        except Exception:
            continue
    return ""


def session_activity(profile: str = "",
                     stale_after_s: float = 24 * 3600.0) -> list[dict]:
    """The profile's sessions with their activity + staleness.

    the Operator's spec (session_activity adapted): each session shows
    whether it is ACTIVE (touched recently) or STALE (no activity in
    `stale_after_s`). The GUI sessions list uses this to show what's
    live vs dormant.
    """
    import time as _t
    from datetime import datetime as _dt
    out = []
    for sid in uuid_session_ids(profile=profile):
        try:
            conn = connect_session(sid, profile=profile, create=False)
            row = conn.execute(
                "SELECT last_active, state, "
                "(SELECT COUNT(*) FROM messages) as msgs "
                "FROM sessions WHERE id=?", (sid,)).fetchone()
            conn.close()
            if row is None:
                continue
            last = row["last_active"] or ""
            try:
                last_ts = _dt.fromisoformat(last).timestamp()
            except Exception:
                last_ts = 0.0
            age = _t.time() - last_ts
            out.append({
                "session_id": sid,
                "last_active": last,
                "age_s": int(max(0, age)),
                "stale": age > stale_after_s,
                "state": row["state"],
                "messages": row["msgs"],
            })
        except Exception:
            continue
    out.sort(key=lambda x: x["age_s"])
    return out


def find_last_session(profile: str = "") -> Optional[str]:
    """The most recently active session for a profile (auto-resume target).

    UUID-ONLY (the Operator's 08-12 strict-name rule): a non-UUID
    session (toolcols/roles/s1) can never be the auto-resume target.
    """
    ids = uuid_session_ids(profile=profile)
    return ids[0] if ids else None


def delete_session(session_id: str, profile: str = "") -> bool:
    """Delete a session file. Returns True when it was removed.

    The Operator's sessions-workspace management: sessions can be deleted
    (removed from disk) or created (a fresh session begins). The current
    session is never deleted — the caller picks a different one first.
    """
    db_cfg = _db_cfg()
    prefix = db_cfg.get("session_prefix", "session-")
    path = sessions_dir(profile) / f"{prefix}{session_id}.db"
    try:
        if path.exists():
            path.unlink()
            return True
    except Exception:
        return False
    return False


def new_session(profile: str = "") -> str:
    """Create a fresh session file; returns its id (a new UUID)."""
    sid = str(uuid.uuid4())
    # Opening the session creates the file + schema.
    conn = connect_session(sid, profile=profile or "default")
    conn.close()
    return sid


def set_session_state(session_id: str, state: str, profile: str = "") -> None:
    """active | idle | ended — persisted on the session row."""
    conn = connect_session(session_id, profile=profile or "default")
    try:
        conn.execute(
            "UPDATE sessions SET state=? WHERE id=?",
            (state, session_id),
        )
        conn.commit()
    finally:
        conn.close()


def set_session_summary(session_id: str, summary: str, profile: str = "") -> None:
    """Persist the rolling summary on the session row (CONTEXT.md bridge)."""
    conn = connect_session(session_id, profile=profile or "default")
    try:
        conn.execute(
            "UPDATE sessions SET summary=? WHERE id=?",
            (summary, session_id),
        )
        conn.commit()
    finally:
        conn.close()


def get_session_summary(session_id: str, profile: str = "") -> str:
    """The session's rolling summary (empty if never compressed)."""
    conn = connect_session(session_id, profile=profile or "default")
    try:
        row = conn.execute(
            "SELECT summary FROM sessions WHERE id=?", (session_id,)
        ).fetchone()
        return (row["summary"] or "") if row else ""
    finally:
        conn.close()


# -- Vault schema (the chronological archive) --------------------------

VAULT_SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    -- SIMPLE variables first (the Operator's order: left = simple → right = complex)
    id             TEXT PRIMARY KEY,  -- column 0: UUID (never duplicates, never collides)
    profile        TEXT NOT NULL,
    source         TEXT,              -- where the entry came from (channel, cli)
    type           TEXT NOT NULL,     -- the TYPE of call: tool | skill | message
    date           TEXT,              -- YYYY-MM-DD (primary sort key) — populated from the moment
    time           TEXT,              -- HH:MM:SS AM|PM (secondary sort key) — populated from the moment
    role           TEXT,              -- who (user | assistant | tool)
    name_first     TEXT,              -- the name GROUP (first + last + nick)
    name_last      TEXT,
    name_nick      TEXT,
    content        TEXT NOT NULL,     -- the entry text
    context        TEXT,              -- ≤128-word semantic summary of what is happening
    setting        TEXT,              -- the details of the environment
    location       TEXT,              -- just where it is happening
    emotion        TEXT,              -- internal feeling
    mood           TEXT,              -- outward display
    activity       TEXT,              -- what the actor is doing
    tool           TEXT,              -- WHICH tool was used for this turn/entry (JSON list)
    tool_call      TEXT,              -- the tool call's arguments (the string used)
    tool_id        TEXT,              -- the tool call's id
    skill          TEXT,              -- WHICH skill was loaded/used for this turn/entry (JSON list)
    skill_call     TEXT,              -- the skill call's arguments (the string used)
    skill_id       TEXT,              -- the skill call's id
    reason         TEXT,              -- the reasoning chain as-is (if applicable)
    reason_start   TEXT,              -- why generation started
    reason_pending TEXT,              -- why generation is pending
    reason_stop    TEXT,              -- why generation stopped (stop|length|tool_calls)
    api_provider   TEXT,              -- which provider served it
    api_model      TEXT,              -- which model produced this entry
    usage_prompt     INTEGER,         -- prompt tokens (OpenAI/Anthropic usage)
    usage_completion INTEGER,         -- completion tokens
    usage_total      INTEGER,         -- total tokens
    response_length    INTEGER,       -- the response-length GROUP
    response_prediction INTEGER,
    response_adjustment INTEGER,
    deleted        INTEGER NOT NULL DEFAULT 0   -- soft-delete flag (recoverable)
);
-- FTS5 is STANDALONE (stores its own copies) because the id is TEXT/UUID —
-- external-content FTS requires an INTEGER rowid. The trigger mirrors rows.
CREATE VIRTUAL TABLE IF NOT EXISTS entries_fts USING fts5(
    id UNINDEXED, content, type, profile, context, location, setting, role
);
CREATE TRIGGER IF NOT EXISTS entries_ai AFTER INSERT ON entries BEGIN
    INSERT INTO entries_fts(id, content, type, profile, context, location, setting, role)
    VALUES (new.id, new.content, new.type, new.profile, new.context, new.location, new.setting, new.role);
END;
CREATE TRIGGER IF NOT EXISTS entries_ad AFTER DELETE ON entries BEGIN
    INSERT INTO entries_fts(entries_fts, id, content, type, profile, context, location, setting, role)
    VALUES ('delete', old.id, old.content, old.type, old.profile, old.context, old.location, old.setting, old.role);
END;
CREATE TRIGGER IF NOT EXISTS entries_au AFTER UPDATE ON entries BEGIN
    DELETE FROM entries_fts WHERE id = old.id;
    INSERT INTO entries_fts(id, content, type, profile, context, location, setting, role)
    VALUES (new.id, new.content, new.type, new.profile, new.context, new.location, new.setting, new.role);
END;
CREATE VIEW IF NOT EXISTS messages AS SELECT * FROM entries WHERE type='message';
CREATE VIEW IF NOT EXISTS tools    AS SELECT * FROM entries WHERE type='tool';
CREATE VIEW IF NOT EXISTS skills   AS SELECT * FROM entries WHERE type='skill';
"""

# -- Session schema (one file per session — the immediate store) --------

SESSION_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,   -- UUID (the session id)
    started_at  TEXT NOT NULL,
    last_active TEXT NOT NULL,
    state       TEXT NOT NULL DEFAULT 'active',  -- active | idle | ended
    profile     TEXT NOT NULL,
    platform    TEXT NOT NULL,      -- which door (http-dashboard / cli / ...)
    summary     TEXT,               -- rolling summary of what happened
    meta        TEXT
);
CREATE TABLE IF NOT EXISTS messages (
    id          TEXT PRIMARY KEY,   -- UUID
    session_id  TEXT NOT NULL REFERENCES sessions(id),
    ts          TEXT NOT NULL,
    role        TEXT NOT NULL,      -- user | assistant | tool | system
    content     TEXT NOT NULL,
    -- the name GROUP (the Operator's bundle)
    name        TEXT,               -- the full participant name
    name_first  TEXT,
    name_last   TEXT,
    name_nick   TEXT,
    -- the tool_call / skill_call GROUPS
    tool_call   TEXT,               -- the tool call reference (LM Studio vocab)
    tool_id     TEXT,               -- the tool call's id
    skill_call  TEXT,               -- OUR skill call reference (parallel to tool_call)
    skill_id    TEXT,               -- the skill call's id
    -- the reason GROUP
    reason_stop    TEXT,           -- why generation stopped
    reason_start   TEXT,           -- why generation started
    reason_pending TEXT,           -- why generation is pending
    -- the api GROUP
    api_provider  TEXT,             -- which provider served it
    api_model     TEXT,             -- which model produced this message
    -- the usage GROUP (token accounting)
    usage_prompt     INTEGER,       -- prompt tokens
    usage_completion INTEGER,       -- completion tokens
    usage_total      INTEGER,       -- total tokens
    seq         INTEGER NOT NULL,   -- order within the session
    -- The response-length GROUP (the Operator's schema: 1 variable = 1 column,
    -- max 3 per group, no duplication — these are real INTEGER columns,
    -- not JSON blobs).
    response_length            INTEGER,  -- actual response word count
    response_prediction        INTEGER,  -- the gauged level's word cap
    response_adjustment        INTEGER,  -- the matching level's word cap
    -- The emotion GROUP (the Operator's 08-11 spec): emotion = the internal
    -- vector snapshot, mood = the outward display.
    emotion        TEXT,               -- internal feeling (vector snapshot)
    mood           TEXT                -- outward display
);
"""
# Indexes created AFTER column migration (imported files may lack
# the columns until then).
SESSION_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, seq);
CREATE INDEX IF NOT EXISTS idx_sessions_active   ON sessions(state, last_active);
"""

# THE SCHEMA-ONCE FLAG (the 08-15 audit): the vault schema runs once per
# process (IF-NOT-EXISTS idempotent) — subsequent connects skip it.
_vault_schema_done = False


def connect_vault(profile: str = "", kind: str = "operator") -> sqlite3.Connection:
    """Open the profile's vault (sessions/vault/vault.db) + ensure schema.

    kind="agent" → the AGENTS' own vault (agent/sessions/vault/vault.db)
    — each session type has its own vault (the Operator's 08-12 split).

    An old-schema vault is REBUILT (the Operator's doctrine): rename to
    vault-old.db, fresh vault, import only the matching columns. The
    function returns a LIVE connection to the current vault.

    THE 08-15 LOCK FIX: the vault runs in WAL mode + a busy timeout —
    concurrent writers (knowledge enrichment, the nurse, a parallel-lane
    worker) no longer hit "disk I/O error" (locked) when the schema runs
    while another connection holds the write lock. WAL lets readers + one
    writer coexist; the busy timeout makes a writer WAIT instead of
    erroring. The schema executescript is also retried once on a lock.
    """
    conn = sqlite3.connect(str(vault_path(profile, kind=kind)),
                           timeout=30.0)
    conn.row_factory = sqlite3.Row
    # THE WAL + BUSY TIMEOUT (the 08-15 fix): WAL journal + a 30s busy
    # wait — the concurrency doctrine for a multi-writer house.
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA synchronous=NORMAL")
    except Exception:
        pass
    conn = _migrate_vault(conn)
    # THE SCHEMA-ONCE FLAG (the 08-15 audit): the schema executescript ran
    # on EVERY connect (each parallel-lane worker + every retrieval opens
    # the vault). The schema is IF-NOT-EXISTS idempotent — run it once per
    # process, then skip. Big win for the parallel lanes.
    global _vault_schema_done
    try:
        if not _vault_schema_done:
            conn.executescript(VAULT_SCHEMA)
            conn.commit()
            _vault_schema_done = True
        else:
            try:
                conn.execute("SELECT 1 FROM entries LIMIT 1")
            except sqlite3.OperationalError:
                # The DB is empty/new (a wiped tree) — run the schema once.
                conn.executescript(VAULT_SCHEMA)
                conn.commit()
                _vault_schema_done = True
    except sqlite3.OperationalError as exc:
        # A lock race (another writer mid-commit) — wait + retry ONCE.
        if "locked" in str(exc).lower() or "disk i/o" in str(exc).lower():
            import time
            time.sleep(0.5)
            try:
                conn.executescript(VAULT_SCHEMA)
                conn.commit()
                _vault_schema_done = True
            except Exception:
                pass
        else:
            raise
    return conn


def _migrate_vault(conn: sqlite3.Connection) -> None:
    """Upgrade an EXISTING vault to the current schema — by REBUILD.

    The Operator's migration doctrine: NO ALTER TABLE surgery. When an old
    vault's schema doesn't match the current one, we:
        1. rename the old vault file to vault-old.db (never deleted)
        2. create a fresh vault.db with the CURRENT schema
        3. import the old rows natively — supplying ONLY the variables
           that exist in the current schema, dropping all others

    A FRESH vault (no entries table) is skipped — the current schema
    creates it correctly.
    """
    has_table = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='entries'"
    ).fetchone()
    if not has_table:
        return conn
    existing = {row[1] for row in conn.execute("PRAGMA table_info(entries)")}

    # The CURRENT schema's entries columns (the only ones we keep).
    import re as _re
    m = _re.search(r"CREATE TABLE IF NOT EXISTS entries \((.*?)\);",
                   VAULT_SCHEMA, _re.S)
    if not m:
        return conn
    current_cols = _re.findall(r"^\s*(\w+)\s", m.group(1), _re.M)
    current_set = set(current_cols)

    # If the vault already has the full current column set AND the type
    # column (no kind) AND the FTS uses type AND the INSERT trigger uses
    # type (not a stale kind), nothing to do.
    fts_ok = True
    try:
        fts_row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name='entries_fts'").fetchone()
        fts_ok = fts_row is not None and "type" in (fts_row[0] or "")
    except Exception:
        fts_ok = False
    trig_ok = True
    try:
        trig_row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='trigger' "
            "AND name='entries_ai'").fetchone()
        trig_ok = trig_row is not None and "kind" not in (trig_row[0] or "")
    except Exception:
        trig_ok = False
    if "type" in existing and current_set <= existing and fts_ok and trig_ok:
        return conn

    # ── REBUILD (the Operator's doctrine): rename → fresh → EXPORT → IMPORT ─
    # 1. Rename vault.db → vault-old.db (KEPT — never delete backups).
    # 2. Create a fresh vault.db with the current schema.
    # 3. EXPORT the old vault's data to JSONL.
    # 4. IMPORT the JSONL into the fresh vault — the import supplies ONLY
    #    the variables the current schema has (date, time, id, content,
    #    the families…), DROPS everything else. Standardized 1:1.
    old_path = vault_path()
    old_backup = old_path.with_name("vault-old.db")
    try:
        conn.close()
        import shutil
        if old_backup.exists():
            old_backup.unlink()  # replace a stale backup (still kept once)
        shutil.copy2(old_path, old_backup)
        old_path.unlink()
        # Fresh vault with the current schema.
        conn2 = sqlite3.connect(str(old_path))
        conn2.row_factory = sqlite3.Row
        conn2.executescript(VAULT_SCHEMA)
        conn2.commit()
        conn2.close()
        # EXPORT the old vault to JSONL (the standardized 1:1 shape).
        old_conn = sqlite3.connect(str(old_backup))
        old_conn.row_factory = sqlite3.Row
        old_cols = [r[1] for r in old_conn.execute("PRAGMA table_info(entries)")]
        # Map the legacy names during export: kind → type (done earlier),
        # tools → tool, skills → skill. ts/name/meta are dropped by the
        # import (they are not in the current schema).
        old_rows = old_conn.execute(
            f"SELECT {', '.join(old_cols)} FROM entries").fetchall()
        lines = []
        for r in old_rows:
            d = dict(r)
            if "kind" in d and "type" not in d:
                d["type"] = d.pop("kind", "message")
            if "tools" in d and "tool" not in d:
                d["tool"] = d.pop("tools", None)
            if "skills" in d and "skill" not in d:
                d["skill"] = d.pop("skills", None)
            # The old display `name` lands in name_nick (the vault's own
            # nickname column) — the name column itself is removed.
            if "name" in d and d.get("name") and "name_nick" not in d:
                d["name_nick"] = d.pop("name")
            # The response-length rename: adjusted → adjustment.
            if "response_adjusted" in d and "response_adjustment" not in d:
                d["response_adjustment"] = d.pop("response_adjusted")
            lines.append(json.dumps(row_to_json(d), ensure_ascii=False))
        old_conn.close()
        # IMPORT the JSONL into the fresh vault (drops non-matching keys).
        import_vault_jsonl("\n".join(lines), profile="")
        # Reopen the caller's connection on the fresh vault.
        conn = sqlite3.connect(str(old_path))
        conn.row_factory = sqlite3.Row
    except Exception as exc:
        from core.logging import log_event
        log_event(4, f"vault rebuild failed: {exc}",
                  source="core", action="migrate_vault")
        try:
            conn = sqlite3.connect(str(old_path))
            conn.row_factory = sqlite3.Row
        except Exception:
            pass
    return conn


def connect_session(session_id: str, *, profile: str = "default",
                    platform: str = "cli", create: bool = True,
                    kind: str = "operator") -> sqlite3.Connection:
    """Open the per-session file in the profile's sessions dir.

    kind="agent" → the profile's agent/sessions/ (the agents' own
    sessions — the Operator's 08-12 split).

    Handles files that already have legacy tables: Athena's columns
    are ADDED via migration (data stays 1:1), and the session row is tagged
    with the profile so auto-resume works.

    create: when True (default), a missing file is created WITH the
    session row (the write path — record_session_message). When False,
    the file is opened READ-ONLY (pure read paths — get_session_history,
    billing, activity): a missing session is never materialized and an
    existing file is never modified (the Operator's 08-12 hygiene rule).
    """
    path = session_path(session_id, profile, kind=kind)
    existed = path.exists()
    if not existed and not create:
        # A read of a non-existent session: return an EMPTY in-memory
        # connection (schema'd) so read queries work — NEVER touch disk.
        mem = sqlite3.connect(":memory:")
        mem.row_factory = sqlite3.Row
        mem.executescript(SESSION_SCHEMA)
        return mem
    # Open read-write (write path) or READ-ONLY (read of an existing
    # file) — mode=ro guarantees a pure read can never write, commit,
    # or clobber the writer's uncommitted messages (the 08-12 deletion
    # fix). The schema/executescript below is harmless on a read-only
    # connection (IF NOT EXISTS against existing tables = no-op).
    if create:
        conn = sqlite3.connect(str(path), timeout=30.0)
    else:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    # THE 08-15 LOCK FIX: the WRITE path runs in WAL + busy timeout so
    # concurrent writers (parallel lanes, the nurse, enrichment) wait
    # instead of erroring with "disk I/O error" (locked).
    if create:
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=30000")
            conn.execute("PRAGMA synchronous=NORMAL")
        except Exception:
            pass
    conn.executescript(SESSION_SCHEMA)
    _migrate_session_tables(conn)
    conn.executescript(SESSION_INDEXES)
    if create:
        conn.commit()
    # Ensure the session row exists (auto-resume friendly). WRITE PATH
    # ONLY (the Operator's 08-12 deletion fix): a read (create=False)
    # NEVER inserts the session row and NEVER commits — a read-only
    # connection committing its snapshot can clobber the writer's
    # uncommitted messages (the chat-history-vanishing race). Reads are
    # pure: open, query, close.
    row = conn.execute("SELECT id FROM sessions WHERE id=?",
                       (session_id,)).fetchone()
    if row is None and create:
        now = datetime.now().isoformat(timespec="seconds")
        # The file may have legacy NOT NULL columns (source, etc.)
        # — satisfy them when present so the insert works on imported files.
        sess_cols = {r[1] for r in conn.execute("PRAGMA table_info(sessions)")}
        import_defaults = {}
        if "source" in sess_cols:
            import_defaults["source"] = "import"
        if "started_at" in sess_cols and "started_at" not in import_defaults:
            import_defaults.setdefault("started_at", now)
        cols = ["id", "state", "profile", "platform", "last_active"]
        vals = [session_id, "active", profile, platform, now]
        for col, val in import_defaults.items():
            if col not in cols:
                cols.append(col)
                vals.append(val)
        conn.execute(
            f"INSERT INTO sessions ({', '.join(cols)})"
            f" VALUES ({', '.join('?' for _ in vals)})",
            vals,
        )
        conn.commit()
    return conn


def _migrate_session_tables(conn: sqlite3.Connection) -> None:
    """Add Athena's columns to a session file that may have legacy tables.

    the legacy state.db has sessions/messages tables with different columns
    (source, session_key, chat_id...) — Athena's view needs state, profile,
    platform, summary, meta on sessions and seq/tool_name on messages.
    Adds missing columns; existing data is untouched.
    """
    sess_cols = {row[1] for row in conn.execute("PRAGMA table_info(sessions)")}
    for col in ("state", "profile", "platform", "summary", "meta",
                "started_at", "last_active"):
        if col not in sess_cols:
            conn.execute(f"ALTER TABLE sessions ADD COLUMN {col} TEXT")
    try:
        msg_cols = {row[1] for row in conn.execute("PRAGMA table_info(messages)")}
        # Rename the OLD response_<x>_<tag> columns to the Operator's clean
        # response_<tag> naming, plus the family renames (the Operator's bundles).
        old_renames = {
            "response_length_prediction": "response_prediction",
            "response_length_adjusted": "response_adjusted",
            "response_adjusted": "response_adjustment",
            "provider": "api_provider",
            "model": "api_model",
            "finish_reason": "reason_finish",
            "reason_finish": "reason_stop",
        }
        for old, new in old_renames.items():
            if old in msg_cols and new not in msg_cols:
                try:
                    conn.execute(f"ALTER TABLE messages RENAME COLUMN {old} TO {new}")
                except Exception:
                    pass
        msg_cols = {row[1] for row in conn.execute("PRAGMA table_info(messages)")}
        # The Operator's vocabulary swap: tool_name → tool_call (LM Studio
        # convention). tool_name is the tool's NAME; tool_call is the call
        # REFERENCE. Existing data carries over (same string).
        if "tool_name" in msg_cols and "tool_call" not in msg_cols:
            try:
                conn.execute("ALTER TABLE messages RENAME COLUMN tool_name TO tool_call")
            except Exception:
                pass
        msg_cols = {row[1] for row in conn.execute("PRAGMA table_info(messages)")}
        for col, decl in (("seq", "INTEGER"),
                          ("session_id", "TEXT"), ("ts", "TEXT"),
                          ("meta", "TEXT"),
                          # the name group
                          ("name", "TEXT"), ("name_first", "TEXT"),
                          ("name_last", "TEXT"), ("name_nick", "TEXT"),
                          # the tool_call / skill_call groups
                          ("tool_call", "TEXT"), ("tool_id", "TEXT"),
                          ("skill_call", "TEXT"), ("skill_id", "TEXT"),
                          # the reason group
                          ("reason_stop", "TEXT"), ("reason_start", "TEXT"),
                          ("reason_pending", "TEXT"),
                          # the api group
                          ("api_provider", "TEXT"), ("api_model", "TEXT"),
                          # the usage group
                          ("usage_prompt", "INTEGER"),
                          ("usage_completion", "INTEGER"),
                          ("usage_total", "INTEGER"),
                          ("response_length", "INTEGER"),
                          ("response_prediction", "INTEGER"),
                          ("response_adjustment", "INTEGER"),
                          # the emotion group (the Operator's 08-11 spec)
                          ("emotion", "TEXT"), ("mood", "TEXT")):
            if col not in msg_cols:
                conn.execute(f"ALTER TABLE messages ADD COLUMN {col} {decl}")
    except Exception:
        pass  # no messages table yet — schema creation handles it


def _similarity(a: str, b: str) -> float:
    """0..1 — how similar two strings are (SequenceMatcher ratio)."""
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def _find_near_duplicate(content: str, threshold: float = 0.90) -> str | None:
    """Return an existing entry id whose content is >= threshold similar.

    The vault keeps OVERLAPPING entries but never exact/near-exact
    duplicates: a session message that matches an existing archive entry
    at 90%+ is considered a duplicate and skipped (dedup, not loss).
    """
    if not content.strip():
        return None
    with connect_vault() as conn:
        # Compare against the most recent entries (the likely overlap zone).
        rows = conn.execute(
            "SELECT id, content FROM entries WHERE deleted=0"
            " ORDER BY rowid DESC LIMIT 200"
        ).fetchall()
    for row in rows:
        if _similarity(content, row["content"] or "") >= threshold:
            return row["id"]
    return None


def record_vault_entry(kind: str | None = None, content: str = "",
                       *, profile: str = "default",
                       kind_sess: str = "operator",
                       source: str = "", role: str = "", context: str = "",
                       type: str | None = None,
                       tool: str | None = None,
                       skill: str | None = None,
                       # the name group (the Operator's bundle)
                       name_first: str | None = None,
                       name_last: str | None = None,
                       name_nick: str | None = None,
                       # the tool_call / skill_call groups (the call column
                       # holds the ARGUMENT STRING used for that call)
                       tool_call: str | None = None,
                       tool_id: str | None = None,
                       skill_call: str | None = None,
                       skill_id: str | None = None,
                       # the reason group (chain + stop + start + pending)
                       reason: str | None = None,
                       reason_stop: str | None = None,
                       reason_start: str | None = None,
                       reason_pending: str | None = None,
                       # the api group
                       api_provider: str | None = None,
                       api_model: str | None = None,
                       # the usage group (token accounting)
                       usage_prompt: int | None = None,
                       usage_completion: int | None = None,
                       usage_total: int | None = None,
                       response_length: int | None = None,
                       response_prediction: int | None = None,
                       response_adjustment: int | None = None,
                       # the emotion group (the Operator's 08-11 spec): emotion
                       # = the internal vector snapshot, mood = the outward
                       # display — both from EMOTION.md after a turn.
                       emotion: str | None = None,
                       mood: str | None = None,
                       dedup: bool = True) -> str:
    """Append one entry to the archive. Returns the entry UUID.

    Native chat-format columns: role, content, date, time, the name group
    (name / name_first / name_last / name_nick), tools / skills (which
    tools and skills were used, as JSON lists), the tool_call group
    (tool_call / tool_id), the skill_call group (skill_call / skill_id),
    the reason group (reason_stop / reason_start / reason_pending), the
    api group (api_provider / api_model) — so the archive reads like a
    chat transcript any LLM tooling can consume directly. The
    response_length* params write to their own INTEGER columns (the Operator's
    schema: 1 variable = 1 column, groups capped at 3).

    dedup=True (default): a content that matches an existing entry at 90%+
    similarity is skipped (returns the existing entry's id) — the vault
    keeps overlapping entries, never exact duplicates.
    """
    # The TYPE of call: tool | skill | message (the Operator's spec). The
    # legacy `kind` param is accepted for backward compatibility; the
    # modern keyword is `type`.
    if type is None:
        type = kind or "message"
    type = str(type).strip().lower() or "message"
    if type not in ("tool", "skill", "message"):
        type = "message"

    if dedup and content.strip():
        existing = _find_near_duplicate(content)
        if existing:
            return existing  # duplicate — skipped, no new row

    entry_id = str(uuid.uuid4())
    now = datetime.now()
    with connect_vault(profile, kind=kind_sess) as conn:
        # legacy vaults have INTEGER entries.id (auto-increment) —
        # in that case let SQLite assign the id instead of inserting a TEXT
        # UUID (datatype mismatch otherwise). Athena-format uses TEXT/UUID.
        id_col_type = next(
            (r[2] for r in conn.execute("PRAGMA table_info(entries)") if r[1] == "id"),
            "TEXT",
        )
        if id_col_type.upper().startswith("INT"):
            conn.execute(
                "INSERT INTO entries (profile, source, type, date, time, role, name_first, name_last, name_nick, content, context, setting, location, emotion, mood, activity, tool, tool_call, tool_id, skill, skill_call, skill_id, reason, reason_start, reason_pending, reason_stop, api_provider, api_model, usage_prompt, usage_completion, usage_total, response_length, response_prediction, response_adjustment)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    profile,
                    source,
                    type,
                    now.strftime("%Y-%m-%d"),
                    now.strftime("%H:%M:%S %p"),
                    role,
                    name_first,
                    name_last,
                    name_nick,
                    content,
                    context,
                    None,  # setting (enrichment fills it)
                    None,  # location (enrichment fills it)
                    emotion,
                    mood,
                    None,  # activity
                    tool,
                    tool_call,
                    tool_id,
                    skill,
                    skill_call,
                    skill_id,
                    reason,
                    reason_start,
                    reason_pending,
                    reason_stop,
                    api_provider,
                    api_model,
                    usage_prompt, usage_completion, usage_total,
                    response_length, response_prediction,
                    response_adjustment,
                ),
            )
            entry_id = str(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        else:
            conn.execute(
                "INSERT INTO entries (id, profile, source, type, date, time, role, name_first, name_last, name_nick, content, context, setting, location, emotion, mood, activity, tool, tool_call, tool_id, skill, skill_call, skill_id, reason, reason_start, reason_pending, reason_stop, api_provider, api_model, usage_prompt, usage_completion, usage_total, response_length, response_prediction, response_adjustment)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    entry_id,
                    profile,
                    source,
                    type,
                    now.strftime("%Y-%m-%d"),
                    now.strftime("%H:%M:%S %p"),
                    role,
                    name_first,
                    name_last,
                    name_nick,
                    content,
                    context,
                    None,  # setting (enrichment fills it)
                    None,  # location (enrichment fills it)
                    emotion,
                    mood,
                    None,  # activity
                    tool,
                    tool_call,
                    tool_id,
                    skill,
                    skill_call,
                    skill_id,
                    reason,
                    reason_start,
                    reason_pending,
                    reason_stop,
                    api_provider,
                    api_model,
                    usage_prompt, usage_completion, usage_total,
                    response_length, response_prediction,
                    response_adjustment,
                ),
            )
    return entry_id


def record_session_message(session_id: str, role: str, content: str,
                           *, tool_call: str | None = None,
                           profile: str = "",
                           kind: str = "operator",
                           meta: dict | None = None,
                           # the name group
                           name: str | None = None,
                           name_first: str | None = None,
                           name_last: str | None = None,
                           name_nick: str | None = None,
                           # the tool_call / skill_call groups
                           tool_id: str | None = None,
                           skill_call: str | None = None,
                           skill_id: str | None = None,
                           # the reason group
                           reason_stop: str | None = None,
                           reason_start: str | None = None,
                           reason_pending: str | None = None,
                           # the api group
                           api_provider: str | None = None,
                           api_model: str | None = None,
                           # the usage group (token accounting)
                           usage_prompt: int | None = None,
                           usage_completion: int | None = None,
                           usage_total: int | None = None,
                           response_length: int | None = None,
                           response_prediction: int | None = None,
                           response_adjustment: int | None = None,
                           # the emotion group (the Operator's 08-11 spec)
                           emotion: str | None = None,
                           mood: str | None = None) -> str:
    """Append one message to a session file. Returns the message UUID.

    profile: tags the session row with the owning profile (so auto-resume
    and listing can filter per agent). First message for a session sets it.
    meta: optional JSON dict for OTHER metadata groups (never the
    response-length trio — those are REAL COLUMNS, 1 variable = 1 column).
    The response_length* params write to their own INTEGER columns (the
    Operator's schema: groups capped at 3, no duplication). The family params
    (name group, tool_call/skill_call groups, reason group, api group)
    write to their own TEXT columns.
    """
    msg_id = str(uuid.uuid4())
    meta_json = json.dumps(meta) if meta else None
    conn = connect_session(session_id, profile=profile or "default",
                           kind=kind)
    try:
        seq = conn.execute(
            "SELECT COALESCE(MAX(seq),0)+1 FROM messages WHERE session_id=?",
            (session_id,),
        ).fetchone()[0]
        # The file may be legacy format where messages.id is INTEGER
        # (auto-increment) — in that case let SQLite assign the id instead
        # of inserting a TEXT UUID (datatype mismatch otherwise).
        id_col_type = next(
            (r[2] for r in conn.execute("PRAGMA table_info(messages)") if r[1] == "id"),
            "TEXT",
        )
        if id_col_type.upper().startswith("INT"):
            conn.execute(
                "INSERT INTO messages (session_id, ts, role, content, name, name_first, name_last, name_nick, tool_call, tool_id, skill_call, skill_id, reason_stop, reason_start, reason_pending, api_provider, api_model, usage_prompt, usage_completion, usage_total, seq, meta, response_length, response_prediction, response_adjustment, emotion, mood)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (session_id, datetime.now().isoformat(timespec="seconds"),
                 role, content, name, name_first, name_last, name_nick,
                 tool_call, tool_id, skill_call, skill_id,
                 reason_stop, reason_start, reason_pending,
                 api_provider, api_model,
                 usage_prompt, usage_completion, usage_total,
                 seq, meta_json,
                 response_length, response_prediction,
                 response_adjustment, emotion, mood),
            )
        else:
            conn.execute(
                "INSERT INTO messages (id, session_id, ts, role, content, name, name_first, name_last, name_nick, tool_call, tool_id, skill_call, skill_id, reason_stop, reason_start, reason_pending, api_provider, api_model, usage_prompt, usage_completion, usage_total, seq, meta, response_length, response_prediction, response_adjustment, emotion, mood)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (msg_id, session_id, datetime.now().isoformat(timespec="seconds"),
                 role, content, name, name_first, name_last, name_nick,
                 tool_call, tool_id, skill_call, skill_id,
                 reason_stop, reason_start, reason_pending,
                 api_provider, api_model,
                 usage_prompt, usage_completion, usage_total,
                 seq, meta_json,
                 response_length, response_prediction,
                 response_adjustment, emotion, mood),
            )
        if profile:
            conn.execute(
                "UPDATE sessions SET profile=? WHERE id=? AND profile='default'",
                (profile, session_id),
            )
        conn.execute(
            "UPDATE sessions SET last_active=? WHERE id=?",
            (datetime.now().isoformat(timespec="seconds"), session_id),
        )
        conn.commit()
    finally:
        conn.close()
    return msg_id


def get_session_history(session_id: str, limit: int = 50,
                        profile: str = "", kind: str = "operator") -> list[dict]:
    """The recent messages for a session. ALWAYS full rows — a row is one
    entry, columns are the pieces; we never return a partial row.

    READ-ONLY (the Operator's 08-12 hygiene rule): never creates the
    session file. If the session doesn't exist yet, returns [] — reads
    must not leave empty orphan files behind.
    """
    path = session_path(session_id, profile or "default", kind=kind)
    if not path.exists():
        return []
    conn = connect_session(session_id, profile=profile or "default",
                           create=False, kind=kind)
    try:
        rows = conn.execute(
            "SELECT * FROM messages WHERE session_id=? ORDER BY seq DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()
        return [dict(row) for row in reversed(rows)]
    finally:
        conn.close()


def count_session_messages(session_id: str, profile: str = "",
                           kind: str = "operator") -> int:
    """Total messages in a session (the chat pagination's page count).

    READ-ONLY (the Operator's 08-12 deletion fix): a count must NEVER
    open the session in write mode — a write-mode open runs the schema
    + migration + commit, which can clobber the writer's uncommitted
    messages when the chat's 1-second tick races the worker. Pure read.
    """
    try:
        path = session_path(session_id, profile or "default", kind=kind)
        if not path.exists():
            return 0
        conn = connect_session(session_id, profile=profile or "default",
                               create=False, kind=kind)
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE session_id=?",
                (session_id,),
            ).fetchone()
            return int(row[0]) if row else 0
        finally:
            conn.close()
    except Exception:
        return 0


def _row_to_jsonl_entry(row: dict) -> dict:
    """The CANONICAL JSONL entry for a message row.

    One row → ONE object. Carries the model-relevant fields (role,
    content, tool when present). Family columns (the response-length
    group) convert to NESTED JSON via row_to_json — the Operator's transport
    mapping: flat SQL columns → one JSON object with the parent key and
    member tags. This is the standardized I/O shape — what export
    produces and import accepts.
    """
    entry = {
        "role": row.get("role", "?"),
        "content": str(row.get("content", "")),
    }
    # OpenAI-compat flat key: a tool message carries its call reference.
    if row.get("tool_call"):
        entry["tool"] = row["tool_call"]
    # Convert the family columns to their nested JSON shape (the Operator's
    # mapping — flat columns → one JSON object per family: response,
    # name, skill_call, reason, api). row_to_json already removed the
    # flat columns and built the groups. The tool_call group is NOT
    # emitted here — the flat `tool` key (OpenAI-compat) IS the transport
    # shape for tool calls; emitting both would duplicate.
    family_json = row_to_json(row)
    for group_key in ("response", "name", "skill_call", "reason", "api",
                      "usage"):
        if family_json.get(group_key):
            entry[group_key] = family_json[group_key]
    return entry


def export_session_jsonl(session_id: str, limit: int = 50,
                         profile: str = "") -> str:
    """Fetch rows from the session .db and render them as JSONL.

    One row → one JSON object per line. The standardized OUTPUT format —
    easy to gather, parse, and hand to the model. The prompt builder's
    History block uses exactly this shape.
    """
    rows = get_session_history(session_id, limit=limit, profile=profile)
    return "\n".join(
        json.dumps(_row_to_jsonl_entry(r), ensure_ascii=False) for r in rows
    )


def import_session_jsonl(session_id: str, jsonl_text: str,
                         profile: str = "") -> int:
    """Supply data as JSONL — one object per line becomes one message row.

    The standardized INPUT format: same shape as export. Accepts the
    canonical entries {"role", "content", "tool"?}; rows are appended in
    order with fresh ids/timestamps/seq. Returns the number imported.
    """
    imported = 0
    conn = connect_session(session_id, profile=profile or "default")
    try:
        seq = conn.execute(
            "SELECT COALESCE(MAX(seq),0)+1 FROM messages WHERE session_id=?",
            (session_id,),
        ).fetchone()[0]
        id_col_type = next(
            (r[2] for r in conn.execute("PRAGMA table_info(messages)") if r[1] == "id"),
            "TEXT",
        )
        now = datetime.now().isoformat(timespec="seconds")
        for line in jsonl_text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict) or "role" not in obj:
                continue
            # Map the nested JSON groups back to flat family columns (the
            # Operator's import mapping: "response": {length, ...} → the
            # response_length trio as SQL columns).
            flat = json_to_row(obj)
            role = str(obj.get("role", "user"))
            content = str(obj.get("content", ""))
            tool = obj.get("tool")
            rl = flat.get("response_length")
            rlp = flat.get("response_prediction")
            rla = flat.get("response_adjustment",
                           flat.get("response_adjusted"))
            nm = flat.get("name")
            nmf = flat.get("name_first")
            nml = flat.get("name_last")
            nmn = flat.get("name_nick")
            tc = flat.get("tool_call") or tool
            tid = flat.get("tool_id")
            sc = flat.get("skill_call")
            sid_ = flat.get("skill_id")
            rf = flat.get("reason_stop")
            rs = flat.get("reason_start")
            rp = flat.get("reason_pending")
            ap = flat.get("api_provider")
            am = flat.get("api_model")
            up = flat.get("usage_prompt")
            uc = flat.get("usage_completion")
            ut = flat.get("usage_total")
            if id_col_type.upper().startswith("INT"):
                conn.execute(
                    "INSERT INTO messages (session_id, ts, role, content, name, name_first, name_last, name_nick, tool_call, tool_id, skill_call, skill_id, reason_stop, reason_start, reason_pending, api_provider, api_model, usage_prompt, usage_completion, usage_total, seq, response_length, response_prediction, response_adjustment)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (session_id, now, role, content, nm, nmf, nml, nmn,
                     tc, tid, sc, sid_, rf, rs, rp, ap, am, up, uc, ut,
                     seq, rl, rlp, rla),
                )
            else:
                conn.execute(
                    "INSERT INTO messages (id, session_id, ts, role, content, name, name_first, name_last, name_nick, tool_call, tool_id, skill_call, skill_id, reason_stop, reason_start, reason_pending, api_provider, api_model, usage_prompt, usage_completion, usage_total, seq, response_length, response_prediction, response_adjustment)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (str(uuid.uuid4()), session_id, now, role, content,
                     nm, nmf, nml, nmn, tc, tid, sc, sid_, rf, rs, rp,
                     ap, am, up, uc, ut, seq, rl, rlp, rla),
                )
            seq += 1
            imported += 1
        conn.execute(
            "UPDATE sessions SET last_active=? WHERE id=?",
            (now, session_id),
        )
        conn.commit()
    finally:
        conn.close()
    return imported


# -- Vault-level export/import (the schema-change contract) -------------
# the Operator's doctrine: when the schema changes and data must survive, the
# path is EXPORT (vault → JSONL) then IMPORT (JSONL → fresh vault). The
# JSONL is the standardized 1:1 shape — the import supplies ONLY the
# variables present in the CURRENT schema, drops everything else. This is
# exactly how a schema change keeps what matters: date, time, id,
# content, the families — imported; anything removed by the schema is not.


def import_vault_jsonl(jsonl_text: str, profile: str = "") -> int:
    """Import JSONL into the CURRENT vault — dropping non-matching keys.

    One line → one row. The canonical shape (role/content/families) maps
    to the current schema's columns via json_to_row; keys the schema
    doesn't have are dropped. The type of call normalizes to
    tool|skill|message. Returns the count imported.
    """
    imported = 0
    conn = connect_vault(profile)
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(entries)")}
        now = datetime.now()
        for line in jsonl_text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict):
                continue
            flat = json_to_row(obj)
            # Keep ONLY columns the current schema has (drop the rest).
            row = {k: v for k, v in flat.items() if k in cols}
            if "id" in cols and row.get("id") is None:
                row["id"] = str(uuid.uuid4())
            if "ts" in cols and row.get("ts") is None:
                row["ts"] = now.isoformat(timespec="seconds")
            if "date" in cols and row.get("date") is None:
                row["date"] = now.strftime("%Y-%m-%d")
            if "time" in cols and row.get("time") is None:
                row["time"] = now.strftime("%H:%M:%S %p")
            if "profile" in cols and row.get("profile") is None:
                row["profile"] = profile or "default"
            if "deleted" in cols and row.get("deleted") is None:
                row["deleted"] = 0
            if "type" in cols and row.get("type") is not None:
                tv = str(row["type"]).strip().lower()
                if tv not in ("tool", "skill", "message"):
                    tv = "message"
                row["type"] = tv
            if "type" in cols and row.get("type") is None:
                row["type"] = "message"
            if "content" in cols and row.get("content") is None:
                row["content"] = ""
            if not row:
                continue
            keys = list(row.keys())
            ph = ", ".join("?" for _ in keys)
            conn.execute(
                f"INSERT OR IGNORE INTO entries ({', '.join(keys)}) "
                f"VALUES ({ph})", [row[k] for k in keys])
            imported += 1
        conn.commit()
    finally:
        conn.close()
    # THE 08-17 INDEX-SYNC (the Operator's spec): the vault changed with an
    # import — rebuild the table of contents so the agent's index queries
    # see the new rows immediately (a fresh, accurate TOC, never stale).
    if imported > 0:
        try:
            build_index(profile)
        except Exception:
            pass
    return imported


# -- Index (the table of contents for the archive) ----------------------

INDEX_SCHEMA = """
CREATE TABLE IF NOT EXISTS sections (
    category    TEXT PRIMARY KEY,   -- the label (kind, role, context...)
    range_from  INTEGER,            -- first vault rowid in this category
    range_to    INTEGER,            -- last vault rowid in this category
    count       INTEGER NOT NULL DEFAULT 0,
    built_at    TEXT
);
"""


def connect_index(profile: str = "") -> sqlite3.Connection:
    """Open the profile's vault/index.db and ensure the schema."""
    conn = sqlite3.connect(str(index_path(profile)))
    conn.row_factory = sqlite3.Row
    conn.executescript(INDEX_SCHEMA)
    conn.commit()
    return conn


def build_index(profile: str = "") -> dict:
    """Scan the profile's vault and rebuild the table of contents.

    Each section is a CATEGORY with the rowid range it covers in the
    archive — a label + pointer, never a copy of the content. The vault
    uses its implicit rowid (chronological insertion order) as the "row
    number", so range_from/range_to point directly at vault rows.
    """
    with connect_vault(profile) as vault:
        rows = vault.execute(
            "SELECT rowid, type, role, context FROM entries WHERE deleted=0"
        ).fetchall()

    sections: dict[str, dict] = {}
    for row in rows:
        rowid = row["rowid"]
        for category in _categories_for(row):
            sec = sections.setdefault(
                category,
                {"category": category, "range_from": rowid, "range_to": rowid, "count": 0},
            )
            sec["range_from"] = min(sec["range_from"], rowid)
            sec["range_to"] = max(sec["range_to"], rowid)
            sec["count"] += 1

    built_at = datetime.now().isoformat(timespec="seconds")
    with connect_index(profile) as idx:
        idx.execute("DELETE FROM sections")
        for sec in sections.values():
            idx.execute(
                "INSERT INTO sections (category, range_from, range_to, count, built_at)"
                " VALUES (?,?,?,?,?)",
                (sec["category"], sec["range_from"], sec["range_to"], sec["count"], built_at),
            )
    return {
        "sections": len(sections),
        "entries": len(rows),
        "built_at": built_at,
    }


def _categories_for(row) -> list[str]:
    """Derive the category labels for one vault row.

    Categories are the vault's own structured cells (kind, role, context) —
    the index labels sections, it never duplicates entry text.
    """
    cats = []
    try:
        kind = row["type"] if "type" in row.keys() else row["kind"]
    except Exception:
        kind = ""
    if kind:
        cats.append(f"kind:{kind}")
    role = row["role"]
    if role:
        cats.append(f"role:{role}")
    context = row["context"]
    if context:
        cats.append(f"context:{context}")
    return cats


def query_index(category: str, limit: int = 20, profile: str = "") -> list[dict]:
    """Look up a category in the profile's TOC. Range + sample FULL rows."""
    conn = connect_index(profile)
    try:
        sec = conn.execute(
            "SELECT category, range_from, range_to, count, built_at FROM sections WHERE category=?",
            (category,),
        ).fetchone()
    finally:
        conn.close()
    if sec is None:
        return []
    result = dict(sec)
    # Resolve the range to FULL rows from the vault (the pointer resolves).
    with connect_vault(profile) as vault:
        sample = vault.execute(
            "SELECT * FROM entries WHERE rowid BETWEEN ? AND ? AND deleted=0 ORDER BY rowid LIMIT ?",
            (sec["range_from"], sec["range_to"], limit),
        ).fetchall()
    result["sample"] = [dict(r) for r in sample]
    return [result]


def list_index(limit: int = 100, profile: str = "") -> list[dict]:
    """All sections in the profile's TOC."""
    conn = connect_index(profile)
    try:
        rows = conn.execute(
            "SELECT category, range_from, range_to, count FROM sections"
            " ORDER BY count DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def health(profile: str = "") -> dict:
    """Cheap health check: can we open both stores for a profile?"""
    result = {"vault": False, "sessions_dir": False}
    try:
        with connect_vault(profile) as conn:
            conn.execute("SELECT 1")
        result["vault"] = True
    except Exception as exc:
        from core.logging import log_event
        log_event(4, f"vault health check failed: {exc}", source="db",
                  action="health")
        result["vault"] = False
    result["sessions_dir"] = sessions_dir(profile).exists()
    return result
