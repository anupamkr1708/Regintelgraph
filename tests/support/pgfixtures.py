"""PostgreSQL test harness. Loaded as a pytest plugin from tests/conftest.py.

DESTRUCTIVE-OPERATION GUARD (docs/phase1/local-postgres-plan.md §3): the harness refuses to run unless the admin DSN points at
a local Unix socket or loopback, and it only ever creates/drops databases named `rig_test_*`. It cannot be pointed at a
remote database by accident. A session-scoped template database has the migrations applied; each test clones it.
(The plan says "each test module clones it"; cloning per TEST is a strictly stronger isolation of the same mechanism.)
"""

from __future__ import annotations

import ipaddress
import os
import re
import uuid
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from scripts.migrate import apply_migrations

_TEST_DB = re.compile(r"^rig_test_[a-z0-9_]{1,48}$")


class UnsafeTestDatabase(Exception):
    """The configured database is not provably local, or its name is not a disposable test name."""


def assert_local_dsn(dsn: str) -> None:
    info = conninfo_to_dict(dsn)
    hosts = [h for h in str(info.get("host") or "").split(",") if h] or [""]
    for host in hosts:
        if host == "" or host.startswith("/"):
            continue  # libpq default socket, or an explicit Unix-socket directory
        try:
            loopback = host == "localhost" or ipaddress.ip_address(host).is_loopback
        except ValueError:
            loopback = False
        if not loopback:
            raise UnsafeTestDatabase(f"refusing to run tests against non-local host {host!r}")
    hostaddr = info.get("hostaddr")
    if hostaddr and not ipaddress.ip_address(hostaddr).is_loopback:
        raise UnsafeTestDatabase("refusing to run tests against a non-loopback hostaddr")


def assert_test_db_name(name: str) -> None:
    if _TEST_DB.fullmatch(name) is None:
        raise UnsafeTestDatabase(f"refusing to create/drop database {name!r}: test databases must be named rig_test_*")


def dsn_for(admin_dsn: str, dbname: str) -> str:
    return make_conninfo(admin_dsn, dbname=dbname)


def _admin(admin_dsn: str) -> psycopg.Connection[Any]:
    assert_local_dsn(admin_dsn)
    return psycopg.connect(admin_dsn, autocommit=True)


def create_db(admin_dsn: str, name: str, template: str | None = None) -> None:
    assert_test_db_name(name)
    if template is not None:
        assert_test_db_name(template)
    with _admin(admin_dsn) as c:
        stmt = sql.SQL("CREATE DATABASE {} ").format(sql.Identifier(name))
        if template:
            stmt += sql.SQL("TEMPLATE {}").format(sql.Identifier(template))
        c.execute(stmt)


def drop_db(admin_dsn: str, name: str) -> None:
    assert_test_db_name(name)
    with _admin(admin_dsn) as c:
        c.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))


@pytest.fixture(scope="session")
def pg_admin_dsn() -> str:
    dsn = os.environ.get("RIG_TEST_DATABASE_URL")
    if not dsn:
        if os.environ.get("RIG_REQUIRE_POSTGRES") == "1":  # CI: a missing database is a FAILURE, never a silent skip
            pytest.fail("RIG_REQUIRE_POSTGRES=1 but RIG_TEST_DATABASE_URL is not set")
        pytest.skip("RIG_TEST_DATABASE_URL not set (local Postgres not available); CI always runs these tests")
    assert_local_dsn(dsn)
    return dsn


@pytest.fixture(scope="session")
def pg_template(pg_admin_dsn: str) -> Iterator[str]:
    name = f"rig_test_tpl_{os.getpid()}"
    drop_db(pg_admin_dsn, name)
    create_db(pg_admin_dsn, name)
    try:
        applied = apply_migrations(dsn_for(pg_admin_dsn, name))
        assert applied, "the template database must be migrated from empty"
        yield name
    finally:
        drop_db(pg_admin_dsn, name)


@pytest.fixture
def pg_dsn(pg_admin_dsn: str, pg_template: str) -> Iterator[str]:
    name = f"rig_test_{uuid.uuid4().hex[:16]}"
    create_db(pg_admin_dsn, name, template=pg_template)
    try:
        yield dsn_for(pg_admin_dsn, name)
    finally:
        drop_db(pg_admin_dsn, name)


@pytest.fixture
def pg_empty_dsn(pg_admin_dsn: str) -> Iterator[str]:
    name = f"rig_test_{uuid.uuid4().hex[:16]}"
    create_db(pg_admin_dsn, name)
    try:
        yield dsn_for(pg_admin_dsn, name)
    finally:
        drop_db(pg_admin_dsn, name)


@pytest.fixture
def pg_conn(pg_dsn: str) -> Iterator[psycopg.Connection[Any]]:
    with psycopg.connect(pg_dsn, autocommit=True) as conn:
        yield conn
