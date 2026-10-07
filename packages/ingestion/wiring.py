"""Runtime wiring of the REAL ingestion dependencies for the CLI's LIVE path. Construction only: nothing here sends a request.

Every location comes from the environment; there are no defaults and no values in Git. Presence of these variables proves nothing
about authorisation: the pipeline's access-review gate still decides, and a LIVE run on a closed gate aborts before any resolver or
transport object is used. Messages name variables, never values (a database URL carries credentials).
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import psycopg

from packages.ingestion.blobstore import FsBlobStore, ensure_disjoint_roots
from packages.ingestion.clock import SystemClock, system_rng
from packages.ingestion.errors import BlobStoreError, WiringError
from packages.ingestion.net import StdlibTransport, SystemResolver
from packages.ingestion.pgrepo import PgRepository
from packages.ingestion.pipeline import Dependencies

ENV_DATABASE_URL = "RIG_DATABASE_URL"
ENV_BLOB_ROOT = "RIG_BLOB_ROOT"
ENV_QUARANTINE_ROOT = "RIG_QUARANTINE_ROOT"
REQUIRED_ENV = (ENV_DATABASE_URL, ENV_BLOB_ROOT, ENV_QUARANTINE_ROOT)


def build_live_dependencies(env: Mapping[str, str]) -> Dependencies:
    missing = [name for name in REQUIRED_ENV if not env.get(name, "").strip()]
    if missing:
        raise WiringError("required environment variables are not set: " + ", ".join(missing))
    blob_root, quarantine_root = Path(env[ENV_BLOB_ROOT]), Path(env[ENV_QUARANTINE_ROOT])
    try:
        ensure_disjoint_roots(blob_root, quarantine_root)
        blobs = FsBlobStore(blob_root)
        quarantine = FsBlobStore(quarantine_root, file_mode=0o400)
    except (BlobStoreError, OSError) as exc:
        raise WiringError(f"blob/quarantine store roots are unusable: {type(exc).__name__}") from exc
    try:
        conn = psycopg.connect(env[ENV_DATABASE_URL], autocommit=True)  # the local system of record; never a source host
    except psycopg.Error as exc:
        raise WiringError(f"database connection failed: {type(exc).__name__}") from exc
    return Dependencies(SystemClock(), system_rng(), SystemResolver(), StdlibTransport(), blobs, quarantine, PgRepository(conn))
