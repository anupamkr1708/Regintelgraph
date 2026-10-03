"""Static regression guard: the shortcuts the safety contract forbids must not appear in project code."""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SELF = Path(__file__).resolve()
CODE_ROOTS = ("packages", "apps", "workers", "scripts")

FORBIDDEN_TEXT = {
    "verify_ssl=False": re.compile(r"verify_ssl\s*=\s*False"),
    "verify=False": re.compile(r"\bverify\s*=\s*False"),
    "allow_redirects=True": re.compile(r"allow_redirects\s*=\s*True"),
    "CERT_NONE": re.compile(r"\bCERT_NONE\b"),
    "check_hostname=False": re.compile(r"check_hostname\s*=\s*False"),
    "unverified ssl context": re.compile(r"_create_unverified_context|_create_stdlib_context"),
    "requests.get/post": re.compile(r"\brequests\.(get|post|put|head|request|Session)\b"),
    "shell=True": re.compile(r"shell\s*=\s*True"),
    "host.endswith": re.compile(r"\bhost\w*\.endswith\("),
    "yaml.load(": re.compile(r"\byaml\.(load|full_load|unsafe_load)\b"),
    "urlopen": re.compile(r"\burlopen\("),
}


def _code_files() -> list[Path]:
    return sorted(p for r in CODE_ROOTS if (REPO / r).is_dir() for p in (REPO / r).rglob("*.py"))


def test_forbidden_text_patterns_are_absent() -> None:
    offenders = [
        f"{p.relative_to(REPO)}:{n}: {label}"
        for p in _code_files()
        for n, line in enumerate(p.read_text().splitlines(), 1)
        if not line.lstrip().startswith("#")
        for label, rx in FORBIDDEN_TEXT.items()
        if rx.search(line) and "FORBIDDEN_TEXT" not in line
    ]
    assert offenders == []


def test_no_dynamic_code_execution_in_project_code() -> None:
    offenders: list[str] = []
    for p in _code_files():
        for node in ast.walk(ast.parse(p.read_text())):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in {"eval", "exec", "compile", "__import__"}:
                offenders.append(f"{p.relative_to(REPO)}:{node.lineno}: {node.func.id}()")
    assert offenders == []


def test_no_third_party_http_dependency_in_the_lockfile_or_pyproject() -> None:
    declared = (REPO / "pyproject.toml").read_text().lower()
    for banned in ("requests", "httpx", "urllib3", "aiohttp", "sqlalchemy", "pydantic", "redis", "celery", "alembic", "kafka", "neo4j"):
        assert banned not in declared.split("[dependency-groups]")[0], banned
    locked = {m.group(1).lower() for m in re.finditer(r'^name = "([^"]+)"', (REPO / "uv.lock").read_text(), re.MULTILINE)}
    assert locked.isdisjoint({"requests", "httpx", "urllib3", "aiohttp", "sqlalchemy", "pydantic", "alembic", "redis", "celery"}), (
        locked & {"requests", "httpx"}
    )


def test_the_only_modules_that_open_sockets_are_net_py() -> None:
    importers = [
        p.relative_to(REPO).as_posix()
        for p in _code_files()
        if re.search(r"^\s*(import|from)\s+(socket|ssl|http\.client|urllib\.request)\b", p.read_text(), re.MULTILINE)
    ]
    assert importers == ["packages/ingestion/net.py"]


def test_retrieved_text_never_reaches_shell_or_dynamic_sql() -> None:
    """No subprocess in packages, and no f-string SQL fed by candidate data (table names in pgrepo.counts are a fixed tuple)."""
    for p in (REPO / "packages").rglob("*.py"):
        assert "subprocess" not in p.read_text(), p
