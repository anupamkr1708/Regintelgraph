"""SECURITY: the CLI is orchestration only. Statically, it cannot talk to the network, parse HTML, or build URLs; it only calls the
pipeline. (Runtime zero-network behaviour is proven by tests/regression/test_live_cli_gate_closed.py.)"""

from __future__ import annotations

import ast
from pathlib import Path

CLI = Path(__file__).resolve().parents[2] / "packages" / "ingestion" / "cli.py"
FORBIDDEN_MODULES = {"socket", "ssl", "http", "urllib", "requests", "httpx", "html", "bs4", "playwright", "selenium", "subprocess"}
FORBIDDEN_PROJECT = {"packages.ingestion.net", "packages.ingestion.egress", "packages.ingestion.discovery", "packages.ingestion.urlpolicy"}


def tree() -> ast.Module:
    return ast.parse(CLI.read_text(encoding="utf-8"))


def imported_modules() -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree()):
        if isinstance(node, ast.Import):
            found |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def test_cli_imports_no_network_html_or_url_policy_module() -> None:
    mods = imported_modules()
    assert not {m for m in mods if m.split(".")[0] in FORBIDDEN_MODULES}
    assert not (mods & FORBIDDEN_PROJECT)


def test_cli_makes_no_transport_resolver_or_fetch_calls() -> None:
    calls = {n.func.attr for n in ast.walk(tree()) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert not (calls & {"open", "resolve", "fetch", "check_url", "urlopen", "connect", "get", "post"})


def test_cli_does_not_touch_the_gate_or_the_limits_or_the_authorisation_state() -> None:
    names = {n.attr for n in ast.walk(tree()) if isinstance(n, ast.Attribute)} | {n.id for n in ast.walk(tree()) if isinstance(n, ast.Name)}
    assert not (names & {"ingestion_authorized", "access_review", "reviewed_by", "reviewed_at", "dry_run_limits", "SafetyLimits"})
    # it reads (never writes) gate_closed_reasons()/crawl for the dry-run report only
    assigns = {t.attr for n in ast.walk(tree()) if isinstance(n, ast.Assign) for t in n.targets if isinstance(t, ast.Attribute)}
    assert not assigns
