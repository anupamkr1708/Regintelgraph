from __future__ import annotations

from pathlib import Path

import pytest

from scripts.check_imports import analyze_source, check_tree

REPO = Path(__file__).resolve().parents[2]


def test_real_repository_has_no_boundary_violations() -> None:
    assert check_tree(REPO) == []


@pytest.mark.parametrize(
    ("path", "source", "needle"),
    [
        ("packages/domain/x.py", "import psycopg", "stdlib-only"),
        ("packages/domain/x.py", "import yaml", "stdlib-only"),
        ("packages/domain/x.py", "from pydantic import BaseModel", "stdlib-only"),
        ("packages/domain/x.py", "import logging", "infrastructure"),
        ("packages/domain/x.py", "import http.client", "network primitive"),
        ("packages/domain/x.py", "from pathlib import Path", "infrastructure"),
        ("packages/domain/x.py", "from packages.ingestion import pipeline", "must not import packages.ingestion"),
        ("packages/domain/x.py", "import packages.retrieval.search", "must not import packages.retrieval"),
        ("packages/ingestion/x.py", "from packages.retrieval import search", "must not import packages.retrieval"),
        ("packages/ingestion/x.py", "import packages.graph", "must not import packages.graph"),
        ("packages/ingestion/x.py", "from packages.agents.router import R", "must not import packages.agents"),
        ("packages/ingestion/x.py", "from apps.api import main", "must not import 'apps"),
        ("packages/ingestion/x.py", "import socket", "network primitive"),
        ("packages/ingestion/other.py", "import ssl", "network primitive"),
        ("packages/ingestion/x.py", "import requests", "HTTP client"),
        ("packages/ingestion/x.py", "import httpx", "HTTP client"),
        ("packages/retrieval/x.py", "from packages.ingestion import pipeline", "must not import packages.ingestion"),
        ("packages/agents/x.py", "from packages.ingestion.pgrepo import PgRepository", "must not import packages.ingestion"),
        ("packages/graph/x.py", "from packages.retrieval import x", "must not import packages.retrieval"),
        ("apps/api/x.py", "import psycopg", "adapters must not import"),
        ("workers/ingestion/x.py", "import yaml", "adapters must not import"),
        ("apps/api/x.py", "from workers.ingestion import main", "another adapter"),
        ("workers/ingestion/x.py", "from apps.api import main", "another adapter"),
        ("apps/api/x.py", "import socket", "network primitive"),
        (
            "packages/ingestion/x.py",
            "from . import y\nfrom .. import retrieval\nfrom ..retrieval import z",
            "must not import packages.retrieval",
        ),
    ],
)
def test_violations_are_detected(path: str, source: str, needle: str) -> None:
    errs = analyze_source(path, source)
    assert any(needle in e for e in errs), errs


@pytest.mark.parametrize(
    ("path", "source"),
    [
        (
            "packages/domain/x.py",
            "import dataclasses, enum, re, uuid, math\nfrom datetime import datetime\nfrom packages.domain.ingest import X",
        ),
        ("packages/ingestion/x.py", "from packages.domain.ingest import X\nimport psycopg, yaml\nfrom . import y"),
        ("packages/ingestion/net.py", "import socket, ssl, http.client"),
        ("packages/agents/x.py", "from packages.retrieval import r\nfrom packages.evidence import e\nfrom packages.domain import d"),
        ("apps/api/x.py", "from packages.ingestion import pipeline\nfrom apps.api import y"),
    ],
)
def test_allowed_imports_pass(path: str, source: str) -> None:
    assert analyze_source(path, source) == []


def test_unparseable_files_are_reported_not_skipped() -> None:
    assert any("cannot parse" in e for e in analyze_source("packages/domain/x.py", "def ("))


def test_domain_package_is_pure_in_reality() -> None:
    import ast
    import sys

    allowed = sys.stdlib_module_names | {"packages"}
    for p in (REPO / "packages/domain").rglob("*.py"):
        for node in ast.walk(ast.parse(p.read_text())):
            if isinstance(node, ast.Import | ast.ImportFrom):
                top = (node.names[0].name if isinstance(node, ast.Import) else node.module or "").split(".")[0]
                assert top in allowed, (p, top)
