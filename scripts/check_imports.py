#!/usr/bin/env python3
"""Import-boundary enforcement (AGENTS.md "Layering rules"; docs/architecture.md §3). Stdlib only, deterministic (AST).

Rules (the dependency map below IS the intended architecture; widening it requires an architecture-doc change, not an edit
made to turn this check green):
  1. `packages/domain` imports no other project package and only the Python standard library — no psycopg, PyYAML, HTTP,
     filesystem, logging, FastAPI or Pydantic.
  2. Each package may import only the packages listed in ALLOWED_PACKAGE_DEPS (ingestion -> domain only, ...). Packages
     never import `apps`, `workers`, `scripts` or `tests`. `agents` never imports `ingestion` (no authoritative writers).
  3. `apps/*` and `workers/*` are thin adapters: they may import packages, never each other / the other adapter family, and
     never `psycopg` / `yaml` directly.
  4. Network primitives (socket, ssl, http.client, urllib.request, ...) are confined to `packages/ingestion/net.py`;
     third-party HTTP clients (requests, httpx, urllib3, aiohttp) are forbidden everywhere.
Limitation (stated, not hidden): static analysis cannot see dynamic imports (`importlib`, `__import__`); `ruff` plus review
cover those.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ALLOWED_PACKAGE_DEPS: dict[str, frozenset[str]] = {
    "domain": frozenset(),
    "ingestion": frozenset({"domain"}),
    "evidence": frozenset({"domain"}),
    "retrieval": frozenset({"domain"}),
    "graph": frozenset({"domain"}),
    "observability": frozenset({"domain"}),
    "agents": frozenset({"domain", "retrieval", "graph", "evidence"}),  # read interfaces only; never `ingestion`
    "evaluation": frozenset({"domain", "retrieval", "graph", "evidence", "agents"}),
}
DOMAIN_FORBIDDEN_STDLIB = frozenset(
    {"os", "pathlib", "shutil", "tempfile", "glob", "subprocess", "socket", "ssl", "http", "urllib", "logging", "sqlite3", "io", "asyncio"}
)
NETWORK_MODULES = (
    "socket",
    "ssl",
    "http.client",
    "http.server",
    "urllib.request",
    "ftplib",
    "smtplib",
    "imaplib",
    "poplib",
    "telnetlib",
    "xmlrpc",
    "socketserver",
)
NETWORK_ALLOWED_FILE = "packages/ingestion/net.py"
BANNED_HTTP_CLIENTS = frozenset({"requests", "httpx", "urllib3", "aiohttp"})
ADAPTER_FORBIDDEN = frozenset({"psycopg", "yaml"})
PROJECT_ROOTS = frozenset({"packages", "apps", "workers", "scripts", "tests"})
SCAN_ROOTS = ("packages", "apps", "workers")


def _owner(rel: str) -> tuple[str, str] | None:
    parts = Path(rel).parts
    if len(parts) >= 2 and parts[0] in SCAN_ROOTS:
        return parts[0], parts[1]
    return None


def _imports(tree: ast.AST, rel: str) -> list[str]:
    """Absolute dotted module names imported by `tree` (relative imports resolved against `rel`)."""
    pkg_parts = list(Path(rel).with_suffix("").parts)[:-1]  # package of the file (also correct for __init__.py)
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = pkg_parts[: len(pkg_parts) - (node.level - 1)] if node.level > 1 else pkg_parts
                module = ".".join([*base, *(node.module.split(".") if node.module else [])])
            else:
                module = node.module or ""
            if module in PROJECT_ROOTS:  # `from packages import ingestion` -> packages.ingestion
                found.extend(f"{module}.{alias.name}" for alias in node.names)
            else:
                found.append(module)
    return found


def analyze_source(rel: str, source: str) -> list[str]:
    """Violations for one file (`rel` is the repo-relative POSIX path). Pure: used directly by the tests."""
    rel = Path(rel).as_posix()
    try:
        tree = ast.parse(source, filename=rel)
    except SyntaxError as exc:
        return [f"{rel}: cannot parse ({exc.msg})"]
    owner = _owner(rel)
    errs: list[str] = []
    stdlib = sys.stdlib_module_names
    for module in _imports(tree, rel):
        top = module.split(".")[0]
        if top in BANNED_HTTP_CLIENTS:
            errs.append(f"{rel}: third-party HTTP client '{module}' is forbidden")
            continue
        if owner is not None and rel != NETWORK_ALLOWED_FILE and any(module == n or module.startswith(n + ".") for n in NETWORK_MODULES):
            errs.append(f"{rel}: network primitive '{module}' is only allowed in {NETWORK_ALLOWED_FILE}")
        if owner is None:
            continue
        kind, name = owner
        if kind == "packages":
            if top in ("apps", "workers", "scripts", "tests"):
                errs.append(f"{rel}: package code must not import '{module}'")
            elif top == "packages":
                segs = module.split(".")
                target = segs[1] if len(segs) > 1 else ""
                if target and target != name and target not in ALLOWED_PACKAGE_DEPS.get(name, frozenset()):
                    errs.append(f"{rel}: packages.{name} must not import packages.{target}")
            if name == "domain" and top != "packages":
                if top not in stdlib:
                    errs.append(f"{rel}: packages.domain must be stdlib-only, found '{module}'")
                elif top in DOMAIN_FORBIDDEN_STDLIB:
                    errs.append(f"{rel}: packages.domain must not import infrastructure module '{module}'")
        else:  # apps/* and workers/* adapters
            if top in ADAPTER_FORBIDDEN:
                errs.append(f"{rel}: adapters must not import '{module}' directly; call a package service")
            if top in ("apps", "workers") and module.split(".")[:2] != [kind, name]:
                errs.append(f"{rel}: adapters must not import another adapter '{module}'")
            if top in ("scripts", "tests"):
                errs.append(f"{rel}: adapters must not import '{module}'")
    return errs


def check_tree(root: Path) -> list[str]:
    errs: list[str] = []
    for scan in SCAN_ROOTS:
        base = root / scan
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.py")):
            rel = path.relative_to(root).as_posix()
            errs.extend(analyze_source(rel, path.read_text(encoding="utf-8")))
    return errs


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    errs = check_tree(root)
    if errs:
        print("\n".join(errs))
        print(f"RESULT: FAILED ({len(errs)} violation(s))")
        return 1
    print("RESULT: import boundaries OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
