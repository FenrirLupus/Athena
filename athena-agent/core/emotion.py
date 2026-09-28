"""Emotion — Athena's internal-state framework (the Operator's spec, 08-11).

Plutchik's wheel mapped to vectors: 8 emotional axes, each a TERNARY range
from -1 (deactivated) to +1 (activated), divided into THREE EQUAL BANDS:

    Low Intensity    -1.00 .. -0.33   → the deactivated named emotion
    Medium Intensity -0.33 .. +0.33   → the neutral named emotion
    High Intensity   +0.33 .. +1.00   → the activated named emotion

8 axes × 3 bands = 24 named emotions. The Emotional Axis Array classifies
where a value LANDS; the pair map (24×24) names what combining two emotions
feels like.

Per profile: EMOTION.md lives in the profile's assistant/ (agent side) and
user/ (operator side). The LLM gauges both sides every turn from the words
spoken; the vector iterates toward accuracy over time. All-zeros start =
neutral (uniform vector — no emotional differential).
"""
from __future__ import annotations

import re
from pathlib import Path

# The 8 axes in wheel order (starting at Joy, clockwise).
AXES = [
    "joy", "trust", "fear", "surprise",
    "sadness", "disgust", "anger", "anticipation",
]

# The 24 named emotions, wheel order, each axis from -1 → 0 → +1.
# (axis, -1 name, 0 name, +1 name)
WHEEL = {
    "joy":          ("Serenity", "Joy", "Ecstasy"),
    "trust":        ("Acceptance", "Trust", "Admiration"),
    "fear":         ("Apprehension", "Fear", "Terror"),
    "surprise":     ("Distraction", "Surprise", "Amazement"),
    "sadness":      ("Pensiveness", "Sadness", "Grief"),
    "disgust":      ("Boredom", "Disgust", "Loathing"),
    "anger":        ("Annoyance", "Anger", "Rage"),
    "anticipation": ("Interest", "Anticipation", "Vigilance"),
}

# The 24 emotions in X/Y order for the pair map (wheel order, low→high).
EMOTION_ORDER = [name for axis in AXES for name in WHEEL[axis]]

# The 24×24 TABLE (the Operator's 08-11 spec): the pair map displayed as a grid.
# Cell (0,0) = NEUTRAL; rows/cols 1..24 = the 24 emotions in wheel order.
# A cell shows the emotion that results from combining its row + column
# emotions. The live vector highlights the active pair's cell.
NEUTRAL = "Neutral"

# name → (axis, band) for the static table (band: -1 low, 0 mid, +1 high).
NAME_INFO = {}
for _axis in AXES:
    for _band, _name in enumerate(WHEEL[_axis]):
        NAME_INFO[_name] = (_axis, _band - 1)

# A representative value per band for static combinations.
_BAND_VALUE = {-1: -0.5, 0: 0.0, 1: 0.5}




# The 24-grid positions: axis index × 3 + band (band -1,0,1 → 0,1,2).
_GRID_POS = {}
for _axis_idx, _axis in enumerate(AXES):
    for _band, _name in enumerate(WHEEL[_axis]):
        _GRID_POS[_name] = _axis_idx * 3 + _band
_GRID_AT = {v: k for k, v in _GRID_POS.items()}


def _midpoint(na: str, nb: str) -> str:
    """The midpoint emotion between two emotions on the 24-grid."""
    pa, pb = _GRID_POS[na], _GRID_POS[nb]
    n = 24
    if pa == pb:
        return na
    if pa > pb:
        pa, pb = pb, pa
    d1 = pb - pa           # clockwise distance
    d2 = n - d1            # counterclockwise distance
    if d1 == d2:
        # Exactly opposite on the wheel — the midpoint is 12 away.
        return _GRID_AT[(pa + 12) % n]
    if d1 < d2:
        steps = d1 // 2
        mid = (pa + steps) % n
        if d1 % 2:
            # Two candidate midpoints — pick the one whose band is
            # closest to the pair's average band (the felt intensity).
            avg_band = ((pa % 3) + (pb % 3)) / 2
            c1 = (pa + steps) % n
            c2 = (pa + steps + 1) % n
            mid = c1 if abs(c1 % 3 - avg_band) <= abs(c2 % 3 - avg_band) else c2
    else:
        steps = d2 // 2
        mid = (pb + steps) % n
        if d2 % 2:
            avg_band = ((pa % 3) + (pb % 3)) / 2
            c1 = (pb + steps) % n
            c2 = (pb + steps + 1) % n
            mid = c1 if abs(c1 % 3 - avg_band) <= abs(c2 % 3 - avg_band) else c2
    return _GRID_AT[mid]


def table_grid() -> list[list[str]]:
    """The 25×25 grid: row/col 0 = NEUTRAL, 1..24 = the 24 emotions.

    Built ONCE by the constraint fill (the Operator's 08-11 spec):
      - Symmetric: (X,Y)==(Y,X), so only the upper triangle is unique.
      - No name appears MORE THAN 2× in any row/column — the strict
        duplicate bound.
      - The enriched vocabulary: dyads (Love, Awe...), blends (Hate,
        Glee, Curiosity...), the midpoint average — every cell one of
        the 48 felt emotions, never an arbitrary pick.
    """
    global _GRID_CACHE
    if _GRID_CACHE is not None:
        return _GRID_CACHE
    _GRID_CACHE = _build_grid()
    return _GRID_CACHE


_GRID_CACHE: list | None = None


def _build_grid() -> list[list[str]]:
    """The constraint fill that guarantees the ≤2-per-row bound."""
    from collections import Counter as _Counter
    grid = [[NEUTRAL for _ in range(25)] for _ in range(25)]
    rowcnt = [_Counter() for _ in range(25)]
    colcnt = [_Counter() for _ in range(25)]

    def can_place(i, j, cand):
        if rowcnt[i].get(cand, 0) >= 2 or colcnt[j].get(cand, 0) >= 2:
            return False
        if i != j and (rowcnt[j].get(cand, 0) >= 2
                       or colcnt[i].get(cand, 0) >= 2):
            return False
        return True

    def place(i, j, cand):
        grid[i][j] = grid[j][i] = cand
        rowcnt[i][cand] += 1
        colcnt[j][cand] += 1
        if i != j:
            rowcnt[j][cand] += 1
            colcnt[i][cand] += 1

    # The preferred name for a pair (dyad at neutral / blend at matched
    # bands / same-axis average) — None when no signature applies.
    def preferred(na, nb):
        ia = NAME_INFO.get(na)
        ib = NAME_INFO.get(nb)
        if ia is None or ib is None:
            return None
        axa, ba = ia
        axb, bb = ib
        if axa == axb:
            avg = (ba + bb) / 2
            return WHEEL[axa][round(avg) + 1]
        key = _pair_key(axa, axb)
        if key in _DYADS and ba == 0 and bb == 0:
            return _DYADS[key]
        if key in _STRONG and ba == bb:
            return _STRONG[key]
        return None

    # The ordered walk for a fill: from the midpoint outward on the
    # 24-grid, so a crowded candidate falls to a nearby name.
    def walk(name):
        p = _GRID_POS[name]
        seen, out = set(), []
        for off in (0, 1, -1, 2, -2, 3, -3, 4, -4, 5, -5,
                    6, -6, 7, -7, 8, -8, 9, -9, 10, -10, 11, -11, 12):
            x = _GRID_AT[(p + off) % 24]
            if x not in seen:
                seen.add(x)
                out.append(x)
        return out

    # Fill order: diagonal first (the matching pairs, the (i,i)
    # coordinates), then off-diagonals by wheel distance.
    pairs = [(i, i) for i in range(1, 25)]
    for d in range(1, 24):
        for i in range(1, 25):
            j = i + d
            if j <= 24:
                pairs.append((i, j))

    for i, j in pairs:
        na = EMOTION_ORDER[i - 1]
        nb = EMOTION_ORDER[j - 1]
        chosen = None
        p = preferred(na, nb)
        if p is not None and can_place(i, j, p):
            chosen = p
        if chosen is None:
            for cand in walk(_midpoint(na, nb)):
                if can_place(i, j, cand):
                    chosen = cand
                    break
        if chosen is None:
            chosen = _midpoint(na, nb)
        place(i, j, chosen)
    return grid


def highlight_cells(vector: dict) -> list[list[int]]:
    """The [row, col] cells of the 24×24 table the live vector activates.

    Each active combination's pair maps to its table positions (1..24);
    the cell where the two meet is the highlighted combination. A lone
    active axis (same+same) highlights its diagonal cell.
    """
    combos = active_combinations(vector)
    cells = []
    for c in combos:
        pair = c.get("pair", [])
        if len(pair) >= 2:
            a = EMOTION_ORDER.index(pair[0]) + 1 if pair[0] in EMOTION_ORDER else 0
            b = EMOTION_ORDER.index(pair[1]) + 1 if pair[1] in EMOTION_ORDER else 0
            if a and b:
                cells.append([a, b])
    return cells

# Band boundaries (the Operator's spec: three EQUAL sections of -1..+1).
LOW_MAX = -0.33   # -1.00 .. -0.33 → deactivated
MID_MAX = +0.33   # -0.33 .. +0.33 → neutral

# The dominant-pair count the pair map combines (the Operator's rule: two).
PAIR_TOP = 2


def band_of(value: float) -> int:
    """Classify a value into its band: -1 (low), 0 (medium), +1 (high)."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0
    if v <= LOW_MAX:
        return -1
    if v >= MID_MAX:
        return 1
    return 0


def emotion_name(axis: str, value: float) -> str:
    """The named emotion for an axis at a value (via the Emotional Axis Array)."""
    names = WHEEL.get(axis, ("", "", ""))
    return names[band_of(value) + 1]


# -- The PAIR MAP (24×24) ------------------------------------------------
# The Operator's cell rules:
#   1. Same + Same → intensity step-up (Joy+Joy → Ecstasy), capped at +1.
#   2. Adjacent axes (wheel neighbors) → Plutchik's primary dyads.
#   3. Opposite axes → the complementary rule: the mix resolves along the
#      perpendicular axis — DOWN to Grief or UP to Ecstasy — chosen by the
#      vector's own lean (which pole the current state already tilts to).
#   4. Everything else → the arc rule (nearest dyad between them).

# Opposite pairs (the Operator's complementary rule): axis → its opposite.
_OPPOSITE = {
    "joy": "sadness", "sadness": "joy",
    "trust": "disgust", "disgust": "trust",
    "fear": "anger", "anger": "fear",
    "surprise": "anticipation", "anticipation": "surprise",
}

# Primary dyads for ADJACENT axes (Plutchik's wheel neighbors). Keys are
# ALPHABETICALLY SORTED axis pairs (the _pair_key convention).
_DYADS = {
    ("joy", "trust"): "Love",
    ("fear", "trust"): "Submission",
    ("fear", "surprise"): "Awe",
    ("sadness", "surprise"): "Disapproval",
    ("disgust", "sadness"): "Remorse",
    ("anger", "disgust"): "Contempt",
    ("anger", "anticipation"): "Aggressiveness",
    ("anticipation", "joy"): "Optimism",
}

# The ENRICHED blends (the Operator's 08-11 spec): OUTSIDE emotion names that
# capture what is felt when mixing two axes — the rich vocabulary beyond
# the base 24. Keyed by the SORTED axis pair; fires at any intensity.
_STRONG = {
    ("anger", "disgust"): "Hate",
    ("anticipation", "joy"): "Glee",
    ("surprise", "trust"): "Curiosity",
    ("anger", "joy"): "Pride",
    ("fear", "joy"): "Guilt",
    ("fear", "sadness"): "Despair",
    ("disgust", "fear"): "Shame",
    ("anger", "sadness"): "Envy",
    ("anticipation", "trust"): "Hope",
    ("anger", "surprise"): "Outrage",
    ("anticipation", "sadness"): "Pessimism",
    ("anticipation", "disgust"): "Cynicism",
    ("joy", "sadness"): "Bittersweet",
    ("disgust", "trust"): "Betrayed",
    ("anger", "fear"): "Cornered",
    ("anticipation", "surprise"): "Bewildered",
}

# Human-readable synonyms for pair results (canonical → reads-as).
_SYNONYMS = {
    "Love": "Affection",
    "Submission": "Yielding",
    "Awe": "Alarm",
    "Disapproval": "Dismay",
    "Remorse": "Guilt",
    "Contempt": "Scorn",
    "Aggressiveness": "Hostility",
    "Optimism": "Hope",
    "Grief": "Devastation",
    "Ecstasy": "Happiness",
    "Sadness": "Melancholy",
    "Relief": "Release",
    "Pensiveness": "Wistful",
    "Amazement": "Wonder",
    "Acceptance": "Resignation",
    "Rage": "Mad",
    "Vigilance": "On edge",
    "Terror": "Paralyzed",
}

# The complementary DOWN pole for strong opposite collisions.
_COMPLEMENT_DOWN = {
    ("anger", "fear"): "Grief",                # the fight lost
    ("disgust", "trust"): "Sadness",           # betrayal's gray (alpha pair key)
    ("anticipation", "surprise"): "Pensiveness",  # the letdown
    ("joy", "sadness"): "Pensiveness",         # melancholy wins the clash
}
# The complementary UP pole (catharsis / release).
_COMPLEMENT_UP = {
    ("anger", "fear"): "Ecstasy",              # fear overcome, anger released
    ("disgust", "trust"): "Relief",            # the betrayal survived
    ("anticipation", "surprise"): "Amazement", # the wait paid off
    ("joy", "sadness"): "Acceptance",          # the memory held warmly
}


def _pair_key(a: str, b: str) -> tuple:
    return (a, b) if a <= b else (b, a)


def combine(axis_a: str, value_a: float,
            axis_b: str, value_b: float) -> dict:
    """The pair-map result for two axes at their values.

    Returns {canonical, synonym, pair} — the name of the combined emotion
    and how it reads. Values come from the Emotional Axis Array (band_of).
    """
    na = emotion_name(axis_a, value_a)
    nb = emotion_name(axis_b, value_b)
    pair = (na, nb)

    # Rule 1: Same + Same → intensity step-up (capped at the +1 name).
    if axis_a == axis_b:
        band = max(band_of(value_a), band_of(value_b))
        step = min(band + 1, 1)
        canonical = WHEEL[axis_a][step + 1]
        return {"canonical": canonical, "synonym": _SYNONYMS.get(canonical, canonical),
                "pair": pair}

    # Rule 3: Opposite axes → the complementary diameter (lean decides).
    if _OPPOSITE.get(axis_a) == axis_b:
        # The lean: which pole the two values tilt toward overall. A
        # positive sum → joyful/up; negative → sad/down.
        lean = value_a + value_b
        key = _pair_key(axis_a, axis_b)
        if lean >= 0:
            canonical = _COMPLEMENT_UP.get(key, "Acceptance")
        else:
            canonical = _COMPLEMENT_DOWN.get(key, "Pensiveness")
        return {"canonical": canonical, "synonym": _SYNONYMS.get(canonical, canonical),
                "pair": pair}

    # Rule 2: Adjacent axes → the primary dyad.
    key = _pair_key(axis_a, axis_b)
    if key in _DYADS:
        canonical = _DYADS[key]
        return {"canonical": canonical, "synonym": _SYNONYMS.get(canonical, canonical),
                "pair": pair}

    # Rule 4: Everything else → the arc rule: the nearest dyad between
    # the two axes on the wheel, weighted toward the stronger axis.
    idx_a, idx_b = AXES.index(axis_a), AXES.index(axis_b)
    n = len(AXES)
    # The shortest arc between the two positions.
    gap = (idx_b - idx_a) % n
    if gap > n // 2:
        gap = n - gap
    step = 1 if abs(value_a) < abs(value_b) else -1
    # Walk from the stronger axis toward the weaker along the arc.
    start = idx_a if abs(value_a) >= abs(value_b) else idx_b
    end = (start + step * gap) % n
    cand = []
    for k in range(0, gap):
        a2 = AXES[(start + step * k) % n]
        b2 = AXES[(start + step * (k + 1)) % n]
        if _pair_key(a2, b2) in _DYADS:
            cand.append(_DYADS[_pair_key(a2, b2)])
    canonical = cand[0] if cand else WHEEL[AXES[end]][0]
    return {"canonical": canonical, "synonym": _SYNONYMS.get(canonical, canonical),
            "pair": pair}


# -- EMOTION.md read / write ---------------------------------------------

def _emotion_path(side: str, profile: str = "") -> Path:
    """The EMOTION.md path for a side (assistant | user) of a profile."""
    try:
        from intelligence.profiles import get_profile, default_profile
        p = get_profile(profile) if profile else None
        root = p.root if p else default_profile().root
    except Exception:
        from core.config import DEFAULT_PROFILE_ROOT
        root = DEFAULT_PROFILE_ROOT
    side_dir = "assistant" if side == "assistant" else "user"
    return root / side_dir / "EMOTION.md"


_DEFAULT_VECTOR = {axis: 0.0 for axis in AXES}


def default_emotion() -> dict:
    """The neutral starting vector (all zeros — uniform = neutral)."""
    return {axis: 0.0 for axis in AXES}


def read_emotion(side: str, profile: str = "") -> dict:
    """Read the EMOTION.md frontmatter for a side. Missing → neutral zeros."""
    path = _emotion_path(side, profile)
    if not path.exists():
        return {"vector": default_emotion(), "current": "neutral — uniform vector",
                "updated": ""}
    try:
        text = path.read_text(encoding="utf-8")
        m = re.search(r"^---\s*\n(.*?)\n---", text, re.S | re.M)
        if not m:
            return {"vector": default_emotion(), "current": "neutral — uniform vector",
                    "updated": ""}
        front = m.group(1)
        vector = {}
        for line in front.splitlines():
            line = line.strip()
            mm = re.match(r"^([a-z_]+):\s*([+-]?[\d.]+)\s*(?:#.*)?$", line)
            if mm and mm.group(1) in AXES:
                vector[mm.group(1)] = float(mm.group(2))
        cur = re.search(r"^current:\s*(.+)$", front, re.M)
        mood = re.search(r"^mood:\s*(.+)$", front, re.M)
        upd = re.search(r"^updated:\s*(.+)$", front, re.M)
        return {
            "vector": {axis: vector.get(axis, 0.0) for axis in AXES},
            "current": (cur.group(1).strip() if cur else
                        (mood.group(1).strip() if mood else _snapshot(vector))),
            "mood": (mood.group(1).strip() if mood else
                     (cur.group(1).strip() if cur else "")),
            "updated": (upd.group(1).strip() if upd else ""),
        }
    except Exception:
        return {"vector": default_emotion(), "current": "neutral — uniform vector",
                "updated": ""}


def _snapshot(vector: dict) -> str:
    """The human-readable one-liner for a vector."""
    active = [(axis, band_of(vector.get(axis, 0.0)))
              for axis in AXES if abs(vector.get(axis, 0.0)) > 0.001]
    if not active:
        return "neutral — uniform vector"
    parts = []
    for axis, band in sorted(active, key=lambda x: -abs(vector.get(x[0], 0.0)))[:3]:
        parts.append(f"{emotion_name(axis, vector.get(axis, 0.0))}({vector.get(axis, 0.0):+.2f})")
    return ", ".join(parts)


def _axis_blends(axis: str) -> list[str]:
    """The blend lines for ONE axis section: its band names + the dyads/
    blends it participates in (the .md's per-axis mapping)."""
    low, mid, high = WHEEL[axis]
    lines = [
        f"- Low (-1.00..-0.33): {low}",
        f"- Neutral (-0.33..+0.33): {mid}",
        f"- High (+0.33..+1.00): {high}",
    ]
    # The blends this axis contributes to (sorted pairs that include it).
    pairs = {}
    for (a, b), name in list(_DYADS.items()) + list(_STRONG.items()):
        if a == axis or b == axis:
            other = b if a == axis else a
            pairs.setdefault(other, []).append(name)
    for other in sorted(pairs, key=lambda x: AXES.index(x) if x in AXES else 99):
        names = pairs[other]
        if len(names) == 1:
            lines.append(f"- Mixed with {other}: {names[0]}")
        else:
            lines.append(f"- Mixed with {other}: {', '.join(names)}")
    return lines


def write_emotion(side: str, profile: str = "",
                  vector: dict | None = None, current: str = "",
                  mood: str = "") -> bool:
    """Rewrite EMOTION.md in the STANDARD Markdown Schema (Operator 08-12):

        ---
        # Emotional Vectors          ← the HEADER (YAML vars only)
        joy: +0.00  # ... (one per axis)
        mood: "..."
        updated: "..."
        ---
                                     ← empty line
        # Joy Axis                   ← the BODY (axis info, NO delims)
        **Serenity → Joy → Ecstasy** ...
        - Mixed with trust: Love
        ... (one axis block per Plutchik axis)
                                     ← empty line
        ---
        # Footer                     ← the FOOTER (closing instruction)
        ---

    Exactly FOUR --- delimiters total: 2 wrap the Header, 2 wrap the
    Footer. The body (axis descriptions) has NO delimiters — the old
    per-section --- framing is gone.
    """
    import datetime
    path = _emotion_path(side, profile)
    vec = {axis: float(vector.get(axis, 0.0)) for axis in AXES} if vector else default_emotion()
    snap = current or _snapshot(vec)
    now = datetime.datetime.now().isoformat(timespec="seconds")
    # THE MOOD CAP (the Operator's 08-15 spec): the mood is the single
    # multi-word sentence (<=64 words) describing how the side feels.
    # When an explicit mood is given, it wins; otherwise fall back to the
    # snapshot one-liner. Enforce the 64-word cap (truncate with ellipsis).
    if mood and str(mood).strip():
        mood_line = str(mood).strip()
        _words = mood_line.split()
        if len(_words) > 64:
            mood_line = " ".join(_words[:64]).rstrip(".,;:") + "…"
    else:
        mood_line = snap

    # HEADER — the emotional vectors (YAML vars). 2 delimiters.
    header_lines = ["# Emotional Vectors",
                    "# Plutchik 8 axes, each -1..+1 (ternary bands)"]
    for axis in AXES:
        low, mid, high = WHEEL[axis]
        header_lines.append(f"{axis}: {vec[axis]:+.2f}  # {low}(-1..-0.33) → {mid}(-0.33..+0.33) → {high}(+0.33..+1)")
    header_lines.append(f"mood: \"{mood_line}\"")
    header_lines.append(f"updated: \"{now}\"")

    # BODY — one axis block per Plutchik axis (NO delimiters inside).
    body_lines = []
    for axis in AXES:
        low, mid, high = WHEEL[axis]
        body_lines.append(f"# {axis.capitalize()} Axis")
        body_lines.append(f"**{low} → {mid} → {high}** — the {axis} band of the wheel")
        body_lines.extend(_axis_blends(axis))
        body_lines.append("")

    # FOOTER — the closing instruction. 2 delimiters.
    footer_lines = ["# Footer",
                    "This file follows the Athena Standard Markdown "
                    "Schema: 4 delimiters total (2 Header, 2 Footer)."]

    lines = ["---"]
    lines.extend(header_lines)
    lines.append("---")
    lines.append("")
    lines.extend(body_lines)
    lines.append("---")
    lines.extend(footer_lines)
    lines.append("---")

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return True
    except Exception:
        return False


# -- The gauge (LLM) + rule-based fallback --------------------------------

def _rule_delta(outcome: dict) -> dict:
    """The tiny deterministic fallback when the LLM gauge is unavailable.

    Rules are the safety net, never the primary path (the Operator's doctrine):
      success → joy +0.2; tool failure → anger -0.3 (annoyance); approval
      held → trust +0.2; repeated failure → sadness -0.2.
    """
    delta = {axis: 0.0 for axis in AXES}
    if outcome.get("exit_reason") in ("completed", "budget_exhausted"):
        delta["joy"] += 0.2
        delta["anticipation"] += 0.1
    failures = outcome.get("tool_failures") or []
    if failures:
        delta["anger"] -= min(0.3 * len(failures), 0.6)
        delta["anticipation"] -= 0.1
    if outcome.get("approval_required"):
        delta["trust"] += 0.2
    return delta


def _apply_delta(vector: dict, delta: dict) -> dict:
    """Clamp each axis to -1..+1 and return the new vector."""
    out = {}
    for axis in AXES:
        v = vector.get(axis, 0.0) + delta.get(axis, 0.0)
        out[axis] = max(-1.0, min(1.0, v))
    return out


def gauge_turn(profile: str, exchange: dict) -> dict:
    """The EMOTION CYCLE step: gauge agent + operator, write both sides.

    The LLM reads the exchange (words spoken) + the previous vector and
    returns the COMPLETE 8-value grid for both sides. Falls back to the
    deterministic rule delta when the gauge call fails. NEVER raises —
    emotion learning never breaks a turn.
    """
    try:
        from core.config import load_config
        cfg = load_config(profile=profile)
        if not cfg.get("emotion", {}).get("enabled", True):
            return {"ok": False, "detail": "emotion disabled"}
    except Exception:
        pass

    prev_agent = read_emotion("assistant", profile)
    prev_user = read_emotion("user", profile)

    # THE CHEAP-TURN GATE (the Operator's efficiency, 08-11): a TRIVIAL
    # exchange (very short words, no tool failures) doesn't need an LLM
    # gauge — the deterministic rule delta is free and correct for the
    # common "hello / ok / thanks" turns. Substantive turns keep the
    # full LLM gauge (the emotion fidelity the Operator spec'd). Configurable:
    #   emotion.llm_gate = false  → always LLM (old behavior)
    #   emotion.min_chars        → the exchange-length threshold.
    # THE EMOTION PROVIDER GATE (the Operator's 08-14 doctrine): emotion
    # is MAINTENANCE, not conversation/tools — the provider is for the
    # operator conversations + tools/skills/workflows, NEVER upkeep. The
    # gauge is FAIL-CLOSED: free by default (deterministic rules), and
    # the LLM gauge runs ONLY when explicitly enabled in config
    # (emotion.llm_gate = true). This kills the per-exchange provider
    # call that was spamming the provider on every turn.
    try:
        from core.config import load_config
        _ecfg = load_config(profile=profile).get("emotion", {})
        llm_gate = bool(_ecfg.get("llm_gate", False))
        min_chars = int(_ecfg.get("min_chars", 12) or 12)
    except Exception:
        llm_gate, min_chars = False, 12
    if llm_gate:
                exchange_len = (str(exchange.get("user_message", "")) +
                                str(exchange.get("reply", "")))
                if len(exchange_len) < min_chars:
                    llm_gate = False
    # THE GAUGE PATH (the Operator's 08-15 audit): the LLM gauge was a
    # REASON call (a separate _call_model per turn) — but the 08-14
    # doctrine says emotion is MAINTENANCE (free, never provider), and
    # the config trim removed emotion.llm_gate so the LLM block was dead
    # code that still logged "emotion gauge unavailable" every turn (a
    # mislabeled message: the gauge wasn't unavailable, it was DISABLED).
    # The deterministic rule path below is the sole gauge — free, no
    # provider, writes both sides. The workflow requirements
    # (emotion_word_valid / mood_within_cap on conversation + roleplay)
    # already drive the MODEL to produce the felt word + mood in its own
    # reply — no separate gauge call is needed.
    # NOTE: llm_gate stays honored for a future explicit re-enable — but
    # the block is intentionally gone: emotion is maintenance, and the
    # rules path is correct and free.
    if llm_gate:
        # THE LEGACY LLM GAUGE (removed 08-15): it duplicated the turn's
        # own requirement fulfillment + spent a provider call per turn on
        # upkeep. The rules path below is the gauge.
        pass

    # Rule-based gauge (agent side only — never fabricate operator
    # emotions without the LLM; the operator's vector is carried forward).
    try:
        delta = _rule_delta(exchange)
        vec_a = _apply_delta(prev_agent.get("vector", default_emotion()), delta)
        write_emotion("assistant", profile, vec_a)
        return {"ok": True, "agent": vec_a, "operator": prev_user.get("vector", default_emotion()),
                "detail": "rule gauge"}
    except Exception as exc:
        try:
            from core.logging import log_event
            log_event(4, f"emotion update failed: {exc}", source="core",
                      action="emotion_update")
        except Exception:
            pass
        return {"ok": False, "detail": "rule gauge failed"}


# -- Active combinations (for the Behavior page) ---------------------------

def active_combinations(vector: dict, top: int = PAIR_TOP) -> list[dict]:
    """The pair-map results for the active axes of a vector.

    The mood LIST (the Operator's 08-11 spec): every active pair that has a
    felt combination — the dominant axis alone (its intensity step),
    then the dominant paired with each other active axis. Each entry is
    {canonical, synonym, pair} from the blend map.
    """
    active = [(axis, vector.get(axis, 0.0)) for axis in AXES
              if abs(vector.get(axis, 0.0)) > 0.001]
    if not active:
        return []
    active.sort(key=lambda x: -abs(x[1]))
    out = []
    # The dominant axis's own intensity feel (the strongest signal).
    a = active[0]
    out.append(combine(a[0], a[1], a[0], a[1]))
    # The dominant paired with each other active axis (the mix feels).
    for b in active[1:]:
        out.append(combine(a[0], a[1], b[0], b[1]))
    return out
