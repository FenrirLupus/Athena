"""Built-in syntax tool — validate code syntax (one script = one tool).

A GENERALIZED everyday tool: validates the syntax of code in common
languages (python, json, java, javascript, yaml, sql, shell) using the
available interpreters/parsers — no execution, just syntax checking.
"""

import json
import shutil
import subprocess


def _check_python(code: str) -> tuple[bool, str]:
    try:
        compile(code, "<syntax>", "exec")
        return True, "valid python"
    except SyntaxError as exc:
        return False, f"python syntax error: line {exc.lineno}: {exc.msg}"


def _check_json(code: str) -> tuple[bool, str]:
    try:
        json.loads(code)
        return True, "valid json"
    except ValueError as exc:
        return False, f"json error: {exc}"


def _check_yaml(code: str) -> tuple[bool, str]:
    try:
        import yaml
        yaml.safe_load(code)
        return True, "valid yaml"
    except Exception as exc:  # noqa: BLE001
        return False, f"yaml error: {exc}"


def _check_shell(code: str) -> tuple[bool, str]:
    bash = shutil.which("bash")
    if not bash:
        return False, "bash not available for syntax check"
    r = subprocess.run([bash, "-n"], input=code, capture_output=True,
                       text=True, timeout=30)
    if r.returncode == 0:
        return True, "valid shell"
    return False, f"shell error: {(r.stderr or r.stdout).strip()[:200]}"


def _check_java(code: str) -> tuple[bool, str]:
    # A best-effort brace/paren balance check (no javac guaranteed).
    if code.count("{") != code.count("}"):
        return False, "java error: unbalanced braces"
    if code.count("(") != code.count(")"):
        return False, "java error: unbalanced parens"
    if code.count("[") != code.count("]"):
        return False, "java error: unbalanced brackets"
    return True, "balanced structure (javac not required for the check)"


def _check_js(code: str) -> tuple[bool, str]:
    node = shutil.which("node")
    if node:
        r = subprocess.run([node, "--check"], input=code, capture_output=True,
                           text=True, timeout=30)
        if r.returncode == 0:
            return True, "valid javascript"
        return False, f"javascript error: {(r.stderr or r.stdout).strip()[:200]}"
    # Fallback: balance check only.
    if code.count("{") != code.count("}"):
        return False, "javascript error: unbalanced braces"
    return True, "balanced structure (node not available for full check)"


_CHECKERS = {
    "python": _check_python,
    "py": _check_python,
    "json": _check_json,
    "yaml": _check_yaml,
    "yml": _check_yaml,
    "shell": _check_shell,
    "bash": _check_shell,
    "sh": _check_shell,
    "java": _check_java,
    "javascript": _check_js,
    "js": _check_js,
}


def _syntax(args: dict, timeout: float = 10.0) -> str:
    lang = str(args.get("language", "")).strip().lower()
    code = str(args.get("code", ""))
    if not lang:
        return "error: language is required"
    checker = _CHECKERS.get(lang)
    if not checker:
        return f"error: unsupported language: {lang} (python/json/yaml/shell/java/javascript)"
    ok, detail = checker(code)
    return json.dumps({"ok": ok, "language": lang, "detail": detail},
                      ensure_ascii=False)


def register() -> list[str]:
    from filesystem.tools import Tool, register
    register(Tool(
        name="syntax",
        description="Validate code syntax without executing: python, json, "
                    "yaml, shell/bash, java, javascript.",
        parameters={
            "type": "object",
            "properties": {
                "language": {"type": "string",
                             "enum": ["python", "json", "yaml", "shell",
                                      "bash", "java", "javascript"]},
                "code": {"type": "string", "description": "The code to check"},
            },
            "required": ["language", "code"],
        },
        fn=_syntax,
    ))
    return ["syntax"]
