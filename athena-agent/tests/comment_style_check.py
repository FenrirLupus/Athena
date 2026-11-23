"""Comments and docstrings describe what the code does — not how it came to be.

The rule this enforces: **a comment states the behaviour of the code it sits on**. It does not narrate history
("this used to…"), cite incidents, dates or audits, name another system, or point at another file. Those belong in
documentation and in the changelog, where a reader looks for them; in a source file they are noise that ages badly and
tells the next reader nothing about what the function actually does.

What is checked: every `#` comment line and every docstring in `.py`, `.sh` and `.js` files. A match is a phrase that
only makes sense as a reference to something outside this file.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Phrases that cannot describe current behaviour: they describe a past state, an incident, a date, or another system.
NARRATIVE = re.compile(
    r"("
    # only unambiguous markers: a sentence that contains one of these can only be describing something that is not
    # this code's current behaviour. Deliberately narrow — "audit trail", "no longer pending" and the model family
    # name "Hermes" are domain vocabulary here and must not be flagged.
    r"(?<!be )\bused to\b|\bpreviously\b|\bUntil now\b|\bthis was\b|\bwas lost\b|"
    r"\bthe old (version|process|rows|behaviour|behavior|name|key|sandboxed|path|fix|value|file)\b|"
    r"\bearlier (multiplexer|version|snapshot|run)\b|\bincident\b|\bbug was found\b|\bregress(ed|ion)\b|"
    r"\bFEN-\d+\b|\b20\d\d-\d\d-\d\d\b|\bturned out\b|"
    r"\bno longer (the|be|the current)\b|\boriginally\b|\bhistorically\b"
    r")",
    re.IGNORECASE,
)

SKIP_DIRS = {"__pycache__", ".git", "node_modules", "staging"}


def comment_lines(path: Path) -> list[tuple[int, str]]:
    """Every comment line in a file, with the line number that starts it."""
    found = []
    text = path.read_text(encoding="utf-8", errors="replace")
    if path.suffix == ".py":
        import ast
        import io
        import tokenize

        try:
            for token in tokenize.generate_tokens(io.StringIO(text).readline):
                if token.type == tokenize.COMMENT:
                    found.append((token.start[0], token.string))
        except (tokenize.TokenError, IndentationError, SyntaxError):
            for number, line in enumerate(text.splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith("#"):
                    found.append((number, stripped))
        return found
    for number, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith(("#", "//")):
            found.append((number, stripped))
    return found


def docstrings(path: Path) -> list[tuple[int, str]]:
    if path.suffix != ".py":
        return []
    import ast

    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return []
    found = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = ast.get_docstring(node) or ""
            if body:
                found.append((getattr(node, "lineno", 0), body))
    return found


def scan(root: Path = ROOT) -> list[dict]:
    """Hits outside this file: the checker quotes the phrases it forbids, so it must not police itself."""
    hits = []
    for path in sorted(Path(root).rglob("*")):
        if not path.is_file() or path.suffix not in {".py", ".sh", ".js"}:
            continue
        if SKIP_DIRS & set(path.parts):
            continue
        if path.name == "comment_style_check.py":
            continue
        for number, text in [*comment_lines(path), *docstrings(path)]:
            for match in NARRATIVE.finditer(text):
                line_start = text.rfind("\n", 0, match.start()) + 1
                line_end = text.find("\n", match.end())
                fragment = text[line_start: line_end if line_end != -1 else len(text)].strip()
                hits.append({"file": str(path.relative_to(root)), "line": number,
                             "phrase": match.group(0), "text": fragment[:160]})
    return hits


def main() -> int:
    hits = scan()
    for hit in hits:
        print(f"  {hit['file']}:{hit['line']}  [{hit['phrase']}]  {hit['text']}")
    files = len({h['file'] for h in hits})
    print(f"{len(hits)} reference(s) in {files} file(s) — RESULT: {'PASS' if not hits else 'FAIL'}")
    return 0 if not hits else 1


if __name__ == "__main__":
    sys.exit(main())
