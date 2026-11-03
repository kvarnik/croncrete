"""Static checks for generated/repaired modules. Reject before import.

Fence: imports only from pydantic/typing/croncrete; no eval-style calls;
step functions take exactly (response, input, prev). Then ruff normalizes
and lints the file. Any failure rejects the module.
"""

from __future__ import annotations

import ast
import shutil
import subprocess
import sys
from pathlib import Path

ALLOWED_IMPORT_ROOTS = {"__future__", "croncrete", "pydantic", "typing"}
BANNED_CALLS = {"breakpoint", "compile", "eval", "exec", "input", "open"}
BANNED_NAMES = {"__import__"}


class CheckError(ValueError):
    pass


def check_source(source: str) -> None:
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        raise CheckError(f"syntax error: {e}") from e
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name.split(".")[0] not in ALLOWED_IMPORT_ROOTS:
                    raise CheckError(f"banned import: {a.name}")
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root not in ALLOWED_IMPORT_ROOTS:
                raise CheckError(f"banned import: {node.module}")
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in BANNED_CALLS
        ):
            raise CheckError(f"banned call: {node.func.id}")
        elif isinstance(node, ast.Name) and node.id in BANNED_NAMES:
            raise CheckError("banned: __import__")
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name.startswith("map_"):
            args = [a.arg for a in node.args.args]
            if args != ["response", "input", "prev"]:
                raise CheckError(f"{node.name} must take (response, input, prev)")


def _ruff() -> str:
    local = Path(sys.executable).parent / "ruff"
    if local.exists():
        return str(local)
    found = shutil.which("ruff")
    if found:
        return found
    raise CheckError("ruff not found")


def lint_file(path: str | Path) -> None:
    """Ruff format + check on trusted scaffolding (no import fence)."""
    ruff = _ruff()
    subprocess.run([ruff, "check", "--fix", str(path)], capture_output=True, text=True)
    fmt = subprocess.run([ruff, "format", str(path)], capture_output=True, text=True)
    if fmt.returncode != 0:
        raise CheckError(f"ruff format failed: {fmt.stderr.strip()}")
    chk = subprocess.run([ruff, "check", str(path)], capture_output=True, text=True)
    if chk.returncode != 0:
        raise CheckError(f"ruff check failed: {chk.stdout.strip()}")


def check_file(path: str | Path) -> None:
    """AST checks, then ruff format + check. Raises CheckError on failure."""
    text = Path(path).read_text()
    check_source(text)
    lint_file(path)
