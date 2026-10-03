#!/usr/bin/env python3
"""Minimal in-repository forward-only SQL migration runner (OD-02, resolved).

Semantics (docs/phase1/tooling-bootstrap-plan.md §6):
  * numbered files `NNNN_name.sql` in `migrations/`, applied in strictly ascending, gap-free order;
  * ONE migration per transaction; the version, name and SHA-256 checksum are recorded in `schema_migration`;
  * re-running applied migrations is a no-op; editing an already-applied file is a HARD failure (never auto-repaired);
  * no down migrations, no autogeneration, no ORM; a session-level advisory lock serialises concurrent runners.
This is repository tooling, not a domain package: it depends only on `psycopg`, so no `packages/*` module imports another
package's infrastructure to reach it.

Usage: python scripts/migrate.py apply|status [--dsn DSN]   (DSN defaults to $RIG_DATABASE_URL)
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import psycopg

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"
_NAME = re.compile(r"^(\d{4})_([a-z0-9][a-z0-9_]*)\.sql$")
_TXN_CONTROL = re.compile(r"^\s*(BEGIN|COMMIT|ROLLBACK|START\s+TRANSACTION|END)\b", re.IGNORECASE | re.MULTILINE)
_DOLLAR_BODY = re.compile(r"\$([A-Za-z_]*)\$.*?\$\1\$", re.DOTALL)
_LINE_COMMENT = re.compile(r"--[^\n]*")
_STRING = re.compile(r"'(?:[^']|'')*'")
_LOCK_KEY = 0x52494754  # arbitrary constant ('RIGT'): all runners contend on the same advisory lock


class MigrationError(Exception):
    """Base class: every subclass is a hard failure that requires a human."""


class ChecksumMismatchError(MigrationError):
    """An already-applied migration file was edited (or is missing). The runner never repairs this."""


class OrderingError(MigrationError):
    """File numbering is not contiguous/unique, or an unapplied migration sorts before an applied one."""


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    name: str
    sql: str
    checksum: str


def _top_level_statements(sql: str) -> str:
    """SQL with dollar-quoted bodies (function code), comments and string literals blanked out, so that only top-level
    statements remain. PL/pgSQL `BEGIN ... END` inside a function body is not transaction control."""
    return _STRING.sub("''", _LINE_COMMENT.sub("", _DOLLAR_BODY.sub("$$$$", sql)))


def discover(directory: Path = MIGRATIONS_DIR) -> list[Migration]:
    found: list[Migration] = []
    for path in sorted(directory.glob("*.sql")):
        match = _NAME.match(path.name)
        if match is None:
            raise OrderingError(f"migration file name must be NNNN_name.sql: {path.name}")
        raw = path.read_bytes()
        sql = raw.decode("utf-8")
        if _TXN_CONTROL.search(_top_level_statements(sql)):
            raise MigrationError(f"{path.name}: transaction control statements are not allowed (the runner owns the transaction)")
        found.append(Migration(int(match.group(1)), match.group(2), sql, hashlib.sha256(raw).hexdigest()))
    versions = [m.version for m in found]
    if versions != list(range(1, len(found) + 1)):
        raise OrderingError(f"migration versions must be unique and contiguous from 0001, found {versions}")
    return found


_BOOTSTRAP = """
CREATE TABLE IF NOT EXISTS schema_migration (
    version    integer PRIMARY KEY CHECK (version >= 1),
    name       text NOT NULL,
    checksum   char(64) NOT NULL CHECK (checksum ~ '^[0-9a-f]{64}$'),
    applied_at timestamptz NOT NULL DEFAULT now()
)
"""


def applied_versions(conn: psycopg.Connection) -> dict[int, tuple[str, str]]:
    rows = conn.execute("SELECT version, name, checksum FROM schema_migration ORDER BY version").fetchall()
    return {int(v): (str(n), str(c).strip()) for v, n, c in rows}


def apply_migrations(dsn: str, directory: Path = MIGRATIONS_DIR) -> list[int]:
    """Apply all pending migrations; return the versions applied by THIS call (empty when nothing was pending)."""
    migrations = discover(directory)
    applied_now: list[int] = []
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("SELECT pg_advisory_lock(%s)", (_LOCK_KEY,))  # concurrent runners queue here, then see a no-op
        try:
            conn.execute(_BOOTSTRAP)
            done = applied_versions(conn)
            by_version = {m.version: m for m in migrations}
            for version, (name, checksum) in done.items():
                migration = by_version.get(version)
                if migration is None:
                    raise ChecksumMismatchError(f"applied migration {version:04d}_{name} has no file")
                if migration.checksum != checksum or migration.name != name:
                    raise ChecksumMismatchError(f"migration {version:04d}_{name} was edited after it was applied; refusing to continue")
            pending = [m for m in migrations if m.version not in done]
            if done and pending and pending[0].version < max(done):
                raise OrderingError("an unapplied migration sorts before an applied one")
            for migration in pending:
                with conn.transaction():  # one migration == one transaction; rolled back as a whole on any error
                    conn.execute(migration.sql)  # file text, not user input
                    conn.execute(
                        "INSERT INTO schema_migration (version, name, checksum) VALUES (%s, %s, %s)",
                        (migration.version, migration.name, migration.checksum),
                    )
                applied_now.append(migration.version)
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (_LOCK_KEY,))
    return applied_now


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["apply", "status"])
    parser.add_argument("--dsn", default=os.environ.get("RIG_DATABASE_URL"))
    args = parser.parse_args(argv)
    if not args.dsn:
        print("error: no DSN (set RIG_DATABASE_URL or pass --dsn)", file=sys.stderr)
        return 2
    try:
        if args.command == "apply":
            done = apply_migrations(args.dsn)
            print(f"applied: {', '.join(f'{v:04d}' for v in done) if done else 'nothing (up to date)'}")
        else:
            with psycopg.connect(args.dsn) as conn:
                conn.execute(_BOOTSTRAP)
                conn.commit()
                for version, (name, checksum) in applied_versions(conn).items():
                    print(f"{version:04d} {name} {checksum[:12]}")
    except MigrationError as exc:
        print(f"MIGRATION FAILED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
