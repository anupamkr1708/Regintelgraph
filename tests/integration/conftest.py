from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import psycopg
import pytest

from packages.ingestion.pgrepo import PgRepository
from packages.ingestion.repository import InMemoryRepository, Repository
from tests.support.env import Env


@pytest.fixture(params=["memory", pytest.param("postgres", marks=pytest.mark.postgres)])
def repo_kind(request: pytest.FixtureRequest) -> str:
    return str(request.param)


@pytest.fixture
def repo(repo_kind: str, request: pytest.FixtureRequest) -> Iterator[Repository]:
    """The SAME contract tests run against the in-memory repository (offline) and PostgreSQL (needs a database)."""
    if repo_kind == "memory":
        yield InMemoryRepository()
        return
    dsn = request.getfixturevalue("pg_dsn")
    with psycopg.connect(dsn, autocommit=True) as conn:
        yield PgRepository(conn)


@pytest.fixture
def env(repo: Repository, tmp_path: Path) -> Env:
    return Env(repo, tmp_path)


@pytest.fixture
def pg_connection(pg_dsn: str) -> Iterator[psycopg.Connection[Any]]:
    with psycopg.connect(pg_dsn, autocommit=True) as conn:
        yield conn
