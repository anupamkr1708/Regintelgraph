#!/usr/bin/env bash
# Project-local PostgreSQL for development (docs/phase1/local-postgres-plan.md, Path A).
#   * socket-only: `listen_addresses = ''` (no TCP listener), Unix socket in a 0700 directory under local_data/
#   * UTC, UTF-8 (C.UTF-8), defaults for everything else (no invented tuning numbers)
#   * NEVER touches a system PostgreSQL cluster; installs NO operating-system packages; Docker is not used
#   * `reset` deletes only local_data/postgres, and only after proving it resolves inside this repository
# Usage: scripts/pg-dev.sh {init|start|stop|status|reset|url|test-url|psql}
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
DATA="$ROOT/local_data/postgres"
SOCK="$ROOT/local_data/pgsock"
LOGDIR="$ROOT/logs"
LOG="$LOGDIR/postgres.log"
DEV_DB="rig_dev"

die() { echo "pg-dev: $*" >&2; exit 1; }

if [ "$(id -u)" -eq 0 ]; then
  die "PostgreSQL refuses to run as root; run this script as an unprivileged user"
fi

find_bin() {
  if [ -n "${RIG_PG_BIN:-}" ]; then echo "$RIG_PG_BIN"; return; fi
  local d
  d="$(ls -d /usr/lib/postgresql/*/bin 2>/dev/null | sort -V | tail -n 1 || true)"
  [ -n "$d" ] || die "PostgreSQL server binaries not found. Install them yourself (Ubuntu: postgresql-16 and postgresql-16-pgvector) or set RIG_PG_BIN. This script installs nothing."
  echo "$d"
}

PGBIN="$(find_bin)"
for tool in initdb pg_ctl pg_isready psql createdb; do
  [ -x "$PGBIN/$tool" ] || [ "$tool" = "psql" ] || [ "$tool" = "createdb" ] || die "missing $PGBIN/$tool"
done

check_socket_len() {
  # Unix socket paths are limited (~107 bytes); fail clearly instead of with an obscure server error.
  [ "${#SOCK}" -lt 90 ] || die "socket directory path too long (${#SOCK} chars): $SOCK"
}

is_running() { "$PGBIN/pg_ctl" -D "$DATA" status >/dev/null 2>&1; }

cmd_init() {
  check_socket_len
  [ ! -e "$DATA" ] || die "$DATA already exists (use 'reset' to recreate it)"
  mkdir -p "$ROOT/local_data" "$LOGDIR"
  mkdir -m 0700 -p "$SOCK"
  "$PGBIN/initdb" -D "$DATA" --encoding=UTF8 --locale=C.UTF-8 --auth-local=trust --auth-host=reject >/dev/null
  {
    echo "# --- RegIntelGraph project-local overrides (scripts/pg-dev.sh) ---"
    echo "listen_addresses = ''"
    echo "unix_socket_directories = '$SOCK'"
    echo "timezone = 'UTC'"
    echo "log_timezone = 'UTC'"
  } >> "$DATA/postgresql.conf"
  echo "initialised $DATA"
}

cmd_start() {
  [ -d "$DATA" ] || die "no cluster; run: scripts/pg-dev.sh init"
  mkdir -p "$LOGDIR"
  if is_running; then echo "already running"; else
    "$PGBIN/pg_ctl" -D "$DATA" -l "$LOG" -w start >/dev/null
  fi
  "$PGBIN/psql" -X -q -h "$SOCK" -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='$DEV_DB'" | grep -q 1 \
    || "$PGBIN/createdb" -h "$SOCK" "$DEV_DB"
  echo "running; socket dir: $SOCK"
}

cmd_stop() {
  [ -d "$DATA" ] || die "no cluster"
  if is_running; then "$PGBIN/pg_ctl" -D "$DATA" -m fast -w stop >/dev/null; echo "stopped"; else echo "not running"; fi
}

cmd_status() { if is_running; then echo "running"; else echo "stopped"; fi; }

cmd_reset() {
  [ -e "$DATA" ] || { cmd_init; cmd_start; return; }
  [ ! -L "$DATA" ] || die "refusing to reset: $DATA is a symlink"
  local real real_root
  real="$(realpath -e -- "$DATA")"
  real_root="$(realpath -e -- "$ROOT")"
  case "$real" in "$real_root"/local_data/postgres) ;; *) die "refusing to reset: $real is not $real_root/local_data/postgres" ;; esac
  [ -f "$real/PG_VERSION" ] || die "refusing to reset: $real does not look like a PostgreSQL data directory"
  if is_running; then cmd_stop; fi
  rm -rf -- "$real"
  cmd_init
  cmd_start
}

user_name="$(id -un)"
cmd_url() { echo "postgresql://${user_name}@/${DEV_DB}?host=${SOCK}"; }
cmd_test_url() { echo "postgresql://${user_name}@/postgres?host=${SOCK}"; }   # maintenance DB: the test harness creates rig_test_* databases

cmd_psql() { exec "$PGBIN/psql" -h "$SOCK" -d "$DEV_DB" "$@"; }

case "${1:-}" in
  init) cmd_init ;;
  start) cmd_start ;;
  stop) cmd_stop ;;
  status) cmd_status ;;
  reset) cmd_reset ;;
  url) cmd_url ;;
  test-url) cmd_test_url ;;
  psql) shift; cmd_psql "$@" ;;
  *) die "usage: $0 {init|start|stop|status|reset|url|test-url|psql}" ;;
esac
