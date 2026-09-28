"""Error classifier — categorize failures for triage (the classifier).

the Operator's spec: before repairing, the system should know WHAT KIND of
failure it is. Categories:
  - transient:   flaky network, timeouts, 5xx, rate limits — retry/hiccup
  - config:      auth, missing keys, bad endpoints, provider not ready
  - logic:       code bugs, schema issues, missing modules — the nurse repairs
  - resource:    disk full, out of memory, no space — the resource manager
  - unknown:     can't tell — treat as logic (surface for the nurse)

The doctor/nurse and the supervisor consult this when a child crashes or
a check fails, so the response matches the failure class.
"""
from __future__ import annotations

import re

TRANSIENT_PATTERNS = re.compile(
    r"timed?\s*out|timeout|connection (refused|reset|aborted)|"
    r"temporary failure|5\d\d|overloaded|rate ?limit|slow ?down|busy|"
    r"econnrefused|econnreset|eagain|eof|network is unreachable",
    re.I,
)
CONFIG_PATTERNS = re.compile(
    r"api[ _]?key|auth|unauthor|forbidden|401|403|not configured|"
    r"no ready provider|invalid (url|endpoint|model)|model not found|"
    r"does not exist.*model|unknown model|endpoint",
    re.I,
)
RESOURCE_PATTERNS = re.compile(
    r"no space left|disk full|out of memory|memoryerror|too many open files|"
    r"e2big|resource temporarily unavailable",
    re.I,
)
LOGIC_PATTERNS = re.compile(
    r"no such (table|column)|syntaxerror|typeerror|attributeerror|"
    r"importerror|modulenotfound|nameerror|valueerror|indexerror|"
    r"keyerror|integrityerror|operationalerror",
    re.I,
)

CATEGORY_LABELS = {
    "transient": "transient — retry/hiccup, no repair needed",
    "config": "config — auth/endpoint/key problem, fix the setup",
    "resource": "resource — disk/memory pressure, free capacity",
    "logic": "logic — code/schema bug, the nurse repairs",
    "unknown": "unknown — surface for the nurse",
}

# Which categories need the NURSE (a repair) vs a retry vs an operator.
NURSE_CATEGORIES = ("logic", "unknown")
RETRY_CATEGORIES = ("transient",)
OPERATOR_CATEGORIES = ("config", "resource")


def classify(error: str | Exception, context: str = "") -> str:
    """Classify an error string (or exception) into a category.

    The first pattern match wins (ordered by specificity).
    """
    text = str(error)
    if context:
        text = f"{context} {text}"
    if RESOURCE_PATTERNS.search(text):
        return "resource"
    if CONFIG_PATTERNS.search(text):
        return "config"
    if TRANSIENT_PATTERNS.search(text):
        return "transient"
    if LOGIC_PATTERNS.search(text):
        return "logic"
    return "unknown"


def describe(error: str | Exception, context: str = "") -> dict:
    """The full triage packet: category + label + suggested action."""
    cat = classify(error, context)
    action = "retry" if cat in RETRY_CATEGORIES else (
        "nurse" if cat in NURSE_CATEGORIES else (
            "operator" if cat in OPERATOR_CATEGORIES else "nurse"))
    return {
        "category": cat,
        "label": CATEGORY_LABELS[cat],
        "action": action,
        "error": str(error)[:300],
    }


def nurse_needed(error: str | Exception, context: str = "") -> bool:
    """Should the nurse be dispatched for this failure?"""
    return classify(error, context) in NURSE_CATEGORIES
