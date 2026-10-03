"""The destructive-operation guard: the test suite can never be pointed at a non-local database or a non-test database name."""

from __future__ import annotations

import pytest

from tests.support.pgfixtures import UnsafeTestDatabase, assert_local_dsn, assert_test_db_name


@pytest.mark.parametrize(
    "dsn",
    [
        "postgresql://u@db.example.com/x",
        "postgresql://u@10.0.0.5/x",
        "postgresql://u@192.0.2.10:5432/x",
        "host=prod.internal dbname=x",
        "postgresql://u@127.0.0.1/x?hostaddr=10.1.2.3",
        "postgresql://u@/x?host=/var/run/postgresql,db.example.com",
        "postgresql://u@[2001:db8::1]/x",
    ],
)
def test_non_local_databases_are_refused(dsn: str) -> None:
    with pytest.raises(UnsafeTestDatabase):
        assert_local_dsn(dsn)


@pytest.mark.parametrize(
    "dsn",
    [
        "postgresql://u@/postgres?host=/home/dev/repo/local_data/pgsock",
        "postgresql://u@localhost/postgres",
        "postgresql://u@127.0.0.1:5432/postgres",
        "postgresql://u@[::1]/postgres",
        "postgresql:///postgres",
        "host=/tmp/sock dbname=postgres",
    ],
)
def test_local_sockets_and_loopback_are_accepted(dsn: str) -> None:
    assert_local_dsn(dsn)


@pytest.mark.parametrize(
    "name",
    [
        "postgres",
        "rig_dev",
        "regintelgraph",
        "production",
        "rig_test_",
        "RIG_TEST_X",
        "rig_test_x;drop database y",
        "rig_test_x y",
        "rig_test_" + "a" * 60,
        'rig_test_"x',
        "",
    ],
)
def test_only_rig_test_names_may_be_created_or_dropped(name: str) -> None:
    with pytest.raises(UnsafeTestDatabase):
        assert_test_db_name(name)


@pytest.mark.parametrize("name", ["rig_test_abc123", "rig_test_tpl_4242", "rig_test_0123456789abcdef"])
def test_valid_test_database_names(name: str) -> None:
    assert_test_db_name(name)
