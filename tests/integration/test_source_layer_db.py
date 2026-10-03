"""Database invariants for the source layer: constraints, provenance, immutability, quarantine gate, atomicity, concurrency."""

from __future__ import annotations

import hashlib
import threading
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
import pytest
from psycopg import errors as pgerr

from packages.domain.content_hash import ContentHash
from packages.domain.ingest import IngestOutcome, RunMode
from packages.ingestion.pgrepo import PgRepository
from packages.ingestion.repository import ArtifactIngest
from tests.support.builders import candidate, pdf
from tests.support.env import Env, one

pytestmark = pytest.mark.postgres
Conn = psycopg.Connection[Any]
T = datetime(2026, 1, 1, tzinfo=UTC)
HASH_A = hashlib.sha256(b"a").hexdigest()
HASH_B = hashlib.sha256(b"b").hexdigest()


def path_of(h: str) -> str:
    return f"{h[:2]}/{h[2:4]}/{h}"


def new_run(c: Conn, *, mode: str = "LIVE", status: str = "COMPLETED", source: str = "src") -> uuid.UUID:
    run_id = uuid.uuid4()
    c.execute(
        "INSERT INTO source (source_id, authority_id, manifest_ref, enabled, created_at) VALUES (%s,'auth','m',true,%s) ON CONFLICT DO NOTHING",
        (source, T),
    )
    c.execute(
        "INSERT INTO ingest_run (ingest_run_id, source_id, mode, status, abort_reason, started_at, finished_at, manifest_hash, manifest_version, code_version,"
        " safety_policy_version, authorisation_ref) VALUES (%s,%s,%s,%s,NULL,%s,%s,%s,'v','c','p','AUTH')",
        (run_id, source, mode, status, T, None if status == "RUNNING" else T, "0" * 64),
    )
    return run_id


def new_artifact(c: Conn, run: uuid.UUID, h: str = HASH_A) -> uuid.UUID:
    aid = uuid.uuid4()
    c.execute(
        "INSERT INTO raw_artifact (raw_artifact_id, content_hash, size_bytes, media_type_sniffed, blob_path_rel, ingest_run_id, created_at) VALUES (%s,%s,1,'application/pdf',%s,%s,%s)",
        (aid, h, path_of(h), run, T),
    )
    return aid


def new_doc(c: Conn, key: str = "src/D1") -> uuid.UUID:
    did = uuid.uuid4()
    c.execute(
        "INSERT INTO regulatory_document (document_id, source_id, authority_id, document_type, title, identity_key, created_at) VALUES (%s,'src','auth','circular','t',%s,%s)",
        (did, key, T),
    )
    return did


def new_version(c: Conn, doc: uuid.UUID, art: uuid.UUID, run: uuid.UUID, h: str = HASH_A) -> uuid.UUID:
    vid = uuid.uuid4()
    c.execute(
        "INSERT INTO document_version (document_version_id, document_id, raw_artifact_id, content_hash, retrieved_at, ingest_run_id, manifest_entry_index,"
        " manifest_status_labels, status) VALUES (%s,%s,%s,%s,%s,%s,0,'{}','STORED')",
        (vid, doc, art, h, T, run),
    )
    return vid


def new_location(c: Conn, version: uuid.UUID, url: str = "https://h.example/f.pdf") -> None:
    c.execute(
        "INSERT INTO document_version_location (document_version_id, canonical_url, first_seen, last_seen) VALUES (%s,%s,%s,%s)",
        (version, url, T, T),
    )


def full_chain(c: Conn) -> dict[str, uuid.UUID]:
    run = new_run(c)
    art = new_artifact(c, run)
    doc = new_doc(c)
    ver = new_version(c, doc, art, run)
    new_location(c, ver)
    return {"run": run, "art": art, "doc": doc, "ver": ver}


# ---- uniqueness / checks / provenance ---------------------------------------------------------------------------------------


def test_same_content_hash_can_only_be_one_raw_artifact(pg_conn: Conn) -> None:
    run = new_run(pg_conn)
    new_artifact(pg_conn, run)
    with pytest.raises(pgerr.UniqueViolation):
        new_artifact(pg_conn, run)


def test_same_document_and_content_can_only_be_one_version(pg_conn: Conn) -> None:
    run, doc = new_run(pg_conn), None
    art = new_artifact(pg_conn, run)
    doc = new_doc(pg_conn)
    new_version(pg_conn, doc, art, run)
    with pytest.raises(pgerr.UniqueViolation):
        new_version(pg_conn, doc, art, run)


def test_a_document_identity_key_is_unique(pg_conn: Conn) -> None:
    new_run(pg_conn)
    new_doc(pg_conn)
    with pytest.raises(pgerr.UniqueViolation):
        new_doc(pg_conn)


@pytest.mark.parametrize("bad", ["", "A" * 64, "a" * 63, "g" * 64, "a" * 64 + " "])
def test_content_hash_format_is_enforced_by_the_database(pg_conn: Conn, bad: str) -> None:
    run = new_run(pg_conn)
    with pytest.raises((pgerr.CheckViolation, pgerr.StringDataRightTruncation)):
        pg_conn.execute(
            "INSERT INTO raw_artifact (raw_artifact_id, content_hash, size_bytes, media_type_sniffed, blob_path_rel, ingest_run_id, created_at)"
            " VALUES (%s,%s,1,'x','x',%s,%s)",
            (uuid.uuid4(), bad, run, T),
        )


def test_storage_path_must_be_derived_from_the_hash(pg_conn: Conn) -> None:
    run = new_run(pg_conn)
    for evil in ["../etc/passwd", f"{HASH_A}", "/abs/" + HASH_A, f"zz/zz/{HASH_A}", f"{HASH_A[:2]}/{HASH_A[2:4]}/other"]:
        with pytest.raises(pgerr.CheckViolation):
            pg_conn.execute(
                "INSERT INTO raw_artifact (raw_artifact_id, content_hash, size_bytes, media_type_sniffed, blob_path_rel, ingest_run_id, created_at)"
                " VALUES (%s,%s,1,'x',%s,%s,%s)",
                (uuid.uuid4(), HASH_A, evil, run, T),
            )


def test_trust_class_and_status_are_constrained(pg_conn: Conn) -> None:
    run = new_run(pg_conn)
    with pytest.raises(pgerr.CheckViolation):
        pg_conn.execute(
            "INSERT INTO raw_artifact (raw_artifact_id, content_hash, size_bytes, media_type_sniffed, blob_path_rel, trust_class, ingest_run_id, created_at)"
            " VALUES (%s,%s,1,'x',%s,'DERIVED_MODEL',%s,%s)",
            (uuid.uuid4(), HASH_A, path_of(HASH_A), run, T),
        )
    art, doc = new_artifact(pg_conn, run), new_doc(pg_conn)
    with pytest.raises(pgerr.CheckViolation):  # PUBLISHED needs parse/index: not representable yet
        pg_conn.execute(
            "INSERT INTO document_version (document_version_id, document_id, raw_artifact_id, content_hash, retrieved_at, ingest_run_id,"
            " manifest_entry_index, manifest_status_labels, status) VALUES (%s,%s,%s,%s,%s,%s,0,'{}','PUBLISHED')",
            (uuid.uuid4(), doc, art, HASH_A, T, run),
        )


def test_no_version_without_a_run_an_artifact_or_a_matching_hash(pg_conn: Conn) -> None:
    run = new_run(pg_conn)
    art, doc = new_artifact(pg_conn, run), new_doc(pg_conn)
    cols = "(document_version_id, document_id, raw_artifact_id, content_hash, retrieved_at, ingest_run_id, manifest_entry_index, manifest_status_labels, status)"
    with pytest.raises(
        pgerr.IntegrityConstraintViolation
    ):  # unknown ingest_run (the LIVE-run trigger fires before the FK; see the catalog test)
        pg_conn.execute(
            f"INSERT INTO document_version {cols} VALUES (%s,%s,%s,%s,%s,%s,0,'{{}}','STORED')",
            (uuid.uuid4(), doc, art, HASH_A, T, uuid.uuid4()),
        )
    with pytest.raises(pgerr.ForeignKeyViolation):  # unknown artifact
        pg_conn.execute(
            f"INSERT INTO document_version {cols} VALUES (%s,%s,%s,%s,%s,%s,0,'{{}}','STORED')",
            (uuid.uuid4(), doc, uuid.uuid4(), HASH_A, T, run),
        )
    with pytest.raises(pgerr.ForeignKeyViolation):  # artifact exists but its hash is a different one: the composite FK ties them together
        pg_conn.execute(
            f"INSERT INTO document_version {cols} VALUES (%s,%s,%s,%s,%s,%s,0,'{{}}','STORED')", (uuid.uuid4(), doc, art, HASH_B, T, run)
        )
    with pytest.raises(pgerr.ForeignKeyViolation):  # unknown document
        pg_conn.execute(
            f"INSERT INTO document_version {cols} VALUES (%s,%s,%s,%s,%s,%s,0,'{{}}','STORED')",
            (uuid.uuid4(), uuid.uuid4(), art, HASH_A, T, run),
        )


@pytest.mark.parametrize(
    "column", ["retrieved_at", "ingest_run_id", "raw_artifact_id", "content_hash", "manifest_status_labels", "manifest_entry_index"]
)
def test_provenance_columns_are_not_null(pg_conn: Conn, column: str) -> None:
    run = new_run(pg_conn)
    art, doc = new_artifact(pg_conn, run), new_doc(pg_conn)
    values: dict[str, Any] = {
        "document_version_id": uuid.uuid4(),
        "document_id": doc,
        "raw_artifact_id": art,
        "content_hash": HASH_A,
        "retrieved_at": T,
        "ingest_run_id": run,
        "manifest_entry_index": 0,
        "manifest_status_labels": "{}",
        "status": "STORED",
    }
    values[column] = None
    names = ", ".join(values)
    # `ingest_run_id` is first rejected by the LIVE-run trigger (a BEFORE trigger fires before NOT NULL is checked); the NOT NULL itself is
    # asserted separately from the catalog below. Every other column is rejected by NOT NULL directly.
    expected = pgerr.IntegrityConstraintViolation if column == "ingest_run_id" else pgerr.NotNullViolation
    with pytest.raises(expected):
        pg_conn.execute(f"INSERT INTO document_version ({names}) VALUES ({', '.join(['%s'] * len(values))})", list(values.values()))


def test_provenance_constraints_exist_independently_of_the_triggers(pg_conn: Conn) -> None:
    """The FK / NOT NULL / composite-FK definitions are in the catalog, so removing a trigger could never silently drop provenance."""
    fks = {
        (r[0], r[1])
        for r in pg_conn.execute(
            "SELECT conrelid::regclass::text, pg_get_constraintdef(oid) FROM pg_constraint WHERE contype = 'f'"
        ).fetchall()
    }
    wanted = {
        ("document_version", "FOREIGN KEY (ingest_run_id) REFERENCES ingest_run(ingest_run_id)"),
        ("document_version", "FOREIGN KEY (document_id) REFERENCES regulatory_document(document_id)"),
        ("document_version", "FOREIGN KEY (raw_artifact_id, content_hash) REFERENCES raw_artifact(raw_artifact_id, content_hash)"),
        ("raw_artifact", "FOREIGN KEY (ingest_run_id) REFERENCES ingest_run(ingest_run_id)"),
        ("document_version_location", "FOREIGN KEY (document_version_id) REFERENCES document_version(document_version_id)"),
    }
    assert wanted <= fks, wanted - fks
    notnull = {
        tuple(r)
        for r in pg_conn.execute(
            "SELECT c.relname, a.attname FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid JOIN pg_namespace n ON n.oid = c.relnamespace"
            " WHERE n.nspname = 'public' AND a.attnum > 0 AND NOT a.attisdropped AND a.attnotnull"
        ).fetchall()
    }
    for col in (
        "ingest_run_id",
        "retrieved_at",
        "raw_artifact_id",
        "content_hash",
        "manifest_status_labels",
        "manifest_entry_index",
        "status",
        "trust_class",
    ):
        assert ("document_version", col) in notnull, col
    for col in ("content_hash", "ingest_run_id", "blob_path_rel", "trust_class", "created_at"):
        assert ("raw_artifact", col) in notnull, col


def test_location_requires_url_and_monotone_times(pg_conn: Conn) -> None:
    c = full_chain(pg_conn)
    with pytest.raises(pgerr.CheckViolation):
        pg_conn.execute(
            "INSERT INTO document_version_location (document_version_id, canonical_url, first_seen, last_seen) VALUES (%s,'u2',%s,%s)",
            (c["ver"], T, T - timedelta(seconds=1)),
        )
    with pytest.raises(pgerr.NotNullViolation):
        pg_conn.execute(
            "INSERT INTO document_version_location (document_version_id, canonical_url, first_seen, last_seen) VALUES (%s,NULL,%s,%s)",
            (c["ver"], T, T),
        )
    with pytest.raises(pgerr.UniqueViolation):
        new_location(pg_conn, c["ver"])  # same (version, url) twice


def test_run_state_constraints(pg_conn: Conn) -> None:
    new_run(pg_conn)  # creates the source
    base = "INSERT INTO ingest_run (ingest_run_id, source_id, mode, status, abort_reason, started_at, finished_at, manifest_hash, manifest_version, code_version, safety_policy_version, authorisation_ref)"
    ok = (
        uuid.uuid4(),
        "src",
        "LIVE",
        "ABORTED",
        "GATE_CLOSED",
        T,
        T,
        "0" * 64,
        "v",
        "c",
        "p",
        None,
    )  # an aborted gate-closed LIVE run needs no authorisation
    pg_conn.execute(base + " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", ok)
    bad_rows = [
        (uuid.uuid4(), "src", "LIVE", "ABORTED", None, T, T, "0" * 64, "v", "c", "p", "A"),  # ABORTED without a reason
        (uuid.uuid4(), "src", "LIVE", "COMPLETED", "GATE_CLOSED", T, T, "0" * 64, "v", "c", "p", "A"),  # reason without ABORTED
        (uuid.uuid4(), "src", "LIVE", "RUNNING", None, T, T, "0" * 64, "v", "c", "p", "A"),  # RUNNING must not be finished
        (uuid.uuid4(), "src", "LIVE", "COMPLETED", None, T, None, "0" * 64, "v", "c", "p", "A"),  # finished run needs finished_at
        (uuid.uuid4(), "src", "LIVE", "COMPLETED", None, T, T, "0" * 64, "v", "c", "p", None),  # LIVE that ran needs authorisation_ref
        (uuid.uuid4(), "src", "LIVE", "COMPLETED", None, T, T - timedelta(1), "0" * 64, "v", "c", "p", "A"),  # finished before started
        (uuid.uuid4(), "src", "WET", "COMPLETED", None, T, T, "0" * 64, "v", "c", "p", "A"),
        (uuid.uuid4(), "src", "LIVE", "COMPLETED", None, T, T, "xyz", "v", "c", "p", "A"),
    ]
    for row in bad_rows:
        with pytest.raises((pgerr.CheckViolation, pgerr.StringDataRightTruncation)):
            pg_conn.execute(base + " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", row)


def test_at_most_one_running_run_per_source(pg_conn: Conn) -> None:
    new_run(pg_conn, status="RUNNING")
    with pytest.raises(pgerr.UniqueViolation, match="one_running"):
        new_run(pg_conn, status="RUNNING")
    new_run(pg_conn, status="RUNNING", source="other")  # another source is independent
    new_run(pg_conn, status="COMPLETED")  # finished runs are unlimited


def test_ingest_result_and_quarantine_constraints(pg_conn: Conn) -> None:
    run = new_run(pg_conn)
    ins = "INSERT INTO ingest_result (ingest_run_id, candidate_key, outcome, attempts, created_at) VALUES (%s,%s,%s,0,%s)"
    pg_conn.execute(ins, (run, "k", "FAILED_PERMANENT", T))
    with pytest.raises(pgerr.UniqueViolation):  # a second result for (run, candidate) is impossible
        pg_conn.execute(ins, (run, "k", "FAILED_TRANSIENT", T))
    with pytest.raises(pgerr.CheckViolation):  # QUARANTINED without a quarantine record
        pg_conn.execute(ins, (run, "k2", "QUARANTINED", T))
    with pytest.raises(pgerr.CheckViolation):  # unknown outcome
        pg_conn.execute(ins, (run, "k3", "PUBLISHED", T))
    with pytest.raises(pgerr.CheckViolation):  # a version outcome must reference the version
        pg_conn.execute(ins, (run, "k4", "NEW_VERSION", T))
    q = "INSERT INTO quarantine_record (quarantine_id, ingest_run_id, candidate_key, reason_code, content_hash, bytes_stored, requested_url, reason_detail, safety_policy_version, created_at)"
    pg_conn.execute(q + " VALUES (%s,%s,'k','SIZE_EXCEEDED',NULL,false,'u','d','p',%s)", (uuid.uuid4(), run, T))
    with pytest.raises(pgerr.UniqueViolation):  # NULLS NOT DISTINCT: a repeated hash-less refusal is the same record
        pg_conn.execute(q + " VALUES (%s,%s,'k','SIZE_EXCEEDED',NULL,false,'u','d','p',%s)", (uuid.uuid4(), run, T))
    with pytest.raises(pgerr.CheckViolation):  # bytes_stored must agree with the presence of a hash
        pg_conn.execute(q + " VALUES (%s,%s,'k2','SIZE_EXCEEDED',%s,false,'u','d','p',%s)", (uuid.uuid4(), run, HASH_A, T))
    with pytest.raises(pgerr.CheckViolation):  # reason codes are a closed list
        pg_conn.execute(q + " VALUES (%s,%s,'k3','LOOKS_FINE',NULL,false,'u','d','p',%s)", (uuid.uuid4(), run, T))


# ---- immutability -------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("table", "update"),
    [
        ("raw_artifact", "size_bytes = 99"),
        ("raw_artifact", "media_type_sniffed = 'x'"),
        ("regulatory_document", "title = 'changed'"),
        ("document_version", "retrieved_at = now()"),
        ("document_version", 'manifest_status_labels = \'{"x":"VERIFIED"}\''),
    ],
)
def test_source_layer_rows_cannot_be_updated_even_by_the_owner(pg_conn: Conn, table: str, update: str) -> None:
    full_chain(pg_conn)
    with pytest.raises(pgerr.IntegrityConstraintViolation, match="append-only"):
        pg_conn.execute(f"UPDATE {table} SET {update}")


@pytest.mark.parametrize("table", ["raw_artifact", "regulatory_document", "document_version", "document_version_location"])
def test_source_layer_rows_cannot_be_deleted_even_by_the_owner(pg_conn: Conn, table: str) -> None:
    full_chain(pg_conn)
    with pytest.raises(pgerr.IntegrityConstraintViolation):
        pg_conn.execute(f"DELETE FROM {table}")
    with pytest.raises(pgerr.IntegrityConstraintViolation):
        pg_conn.execute(f"DELETE FROM {table} WHERE true")


def test_runtime_result_quarantine_and_event_rows_are_append_only(pg_conn: Conn) -> None:
    run = new_run(pg_conn)
    pg_conn.execute(
        "INSERT INTO ingest_result (ingest_run_id, candidate_key, outcome, attempts, created_at) VALUES (%s,'k','FAILED_PERMANENT',0,%s)",
        (run, T),
    )
    pg_conn.execute(
        "INSERT INTO quarantine_record (quarantine_id, ingest_run_id, candidate_key, reason_code, bytes_stored, requested_url, reason_detail, safety_policy_version, created_at)"
        " VALUES (%s,%s,'k','SIZE_EXCEEDED',false,'u','d','p',%s)",
        (uuid.uuid4(), run, T),
    )
    jid = uuid.uuid4()
    pg_conn.execute(
        "INSERT INTO job (job_id, kind, payload, idempotency_key, state, max_attempts, run_after, created_at, updated_at) VALUES (%s,'k','{}','i','QUEUED',1,%s,%s,%s)",
        (jid, T, T, T),
    )
    pg_conn.execute("INSERT INTO job_event (job_id, event_type, at) VALUES (%s,'ENQUEUED',%s)", (jid, T))
    for stmt in (
        "UPDATE ingest_result SET attempts = 5",
        "DELETE FROM ingest_result",
        "UPDATE quarantine_record SET reason_detail = 'x'",
        "DELETE FROM quarantine_record",
        "UPDATE job_event SET detail = 'x'",
        "DELETE FROM job_event",
    ):
        with pytest.raises(pgerr.IntegrityConstraintViolation):
            pg_conn.execute(stmt)


def test_location_last_seen_is_the_only_mutable_field_and_only_moves_forward(pg_conn: Conn) -> None:
    c = full_chain(pg_conn)
    pg_conn.execute("UPDATE document_version_location SET last_seen = %s", (T + timedelta(days=1),))  # allowed
    assert pg_conn.execute("SELECT last_seen FROM document_version_location").fetchone() == (T + timedelta(days=1),)
    with pytest.raises(pgerr.IntegrityConstraintViolation, match="forward"):
        pg_conn.execute("UPDATE document_version_location SET last_seen = %s", (T,))
    for col, val in [("canonical_url", "'https://other'"), ("first_seen", "now()"), ("http_meta", "'{\"x\":1}'")]:
        with pytest.raises(pgerr.IntegrityConstraintViolation, match=r"only document_version_location\.last_seen"):
            pg_conn.execute(f"UPDATE document_version_location SET {col} = {val}")
    with pytest.raises(pgerr.IntegrityConstraintViolation):
        pg_conn.execute("UPDATE document_version_location SET document_version_id = %s", (uuid.uuid4(),))
    assert c["ver"]  # the version row itself stayed untouched throughout


# ---- least privilege (app role) --------------------------------------------------------------------------------------------------


def test_app_role_has_least_privilege(pg_conn: Conn) -> None:
    c = full_chain(pg_conn)
    assert pg_conn.execute("SELECT rolsuper, rolcreatedb, rolcreaterole, rolcanlogin FROM pg_roles WHERE rolname='rig_app'").fetchone() == (
        False,
        False,
        False,
        False,
    )
    pg_conn.execute("SET ROLE rig_app")
    try:
        assert pg_conn.execute("SELECT count(*) FROM raw_artifact").fetchone() == (1,)  # can read
        for stmt in (
            "UPDATE raw_artifact SET size_bytes = 5",
            "DELETE FROM raw_artifact",
            "UPDATE document_version SET status = 'STORED'",
            "DELETE FROM document_version",
            "UPDATE regulatory_document SET title = 'x'",
            "DELETE FROM quarantine_record",
            "UPDATE ingest_result SET attempts = 1",
            "TRUNCATE raw_artifact",
            "UPDATE document_version_location SET http_meta = '{}'",
            "DELETE FROM document_version_location",
            "UPDATE schema_migration SET name = 'x'",
            "CREATE TABLE evil (id int)",
            "COPY (SELECT 1) TO PROGRAM 'true'",
            "CREATE ROLE evil",
            "DROP TABLE raw_artifact",
        ):
            with pytest.raises((pgerr.InsufficientPrivilege, pgerr.UndefinedTable)):
                pg_conn.execute(stmt)
        pg_conn.execute("UPDATE document_version_location SET last_seen = %s", (T + timedelta(hours=1),))  # the single column it may update
        assert c["ver"]
    finally:
        pg_conn.execute("RESET ROLE")


def test_app_role_can_do_what_ingestion_needs(pg_dsn: str, tmp_path: Path) -> None:
    """The full pipeline runs as rig_app (not the owner), proving the grants are sufficient and nothing more is needed."""
    with psycopg.connect(pg_dsn, autocommit=True) as conn:
        conn.execute("SET ROLE rig_app")
        env = Env(PgRepository(conn), tmp_path)
        env.serve("TEST-001", pdf("app-role"))
        r = env.run([one()]).results[0]
        assert r.outcome is IngestOutcome.NEW_VERSION
        env.serve("TEST-001", pdf("app-role"))
        assert env.run([one()]).results[0].outcome is IngestOutcome.UNCHANGED_SKIPPED  # includes the last_seen UPDATE


# ---- quarantine gate / live-only ----------------------------------------------------------------------------------------------


def quarantine_hash(c: Conn, run: uuid.UUID, h: str) -> None:
    c.execute(
        "INSERT INTO quarantine_record (quarantine_id, ingest_run_id, candidate_key, reason_code, content_hash, bytes_stored, requested_url, reason_detail, safety_policy_version, created_at)"
        " VALUES (%s,%s,'k','PDF_SANITY_FAILED',%s,true,'u','d','p',%s)",
        (uuid.uuid4(), run, h, T),
    )


def test_content_with_a_quarantine_record_cannot_become_source_content(pg_conn: Conn) -> None:
    run = new_run(pg_conn)
    quarantine_hash(pg_conn, run, HASH_A)
    with pytest.raises(pgerr.IntegrityConstraintViolation, match="quarantine"):
        new_artifact(pg_conn, run, HASH_A)
    new_artifact(pg_conn, run, HASH_B)  # unrelated content is unaffected


def test_a_version_cannot_be_created_for_content_quarantined_after_its_artifact_existed(pg_conn: Conn) -> None:
    run = new_run(pg_conn)
    art, doc = new_artifact(pg_conn, run), new_doc(pg_conn)
    quarantine_hash(pg_conn, run, HASH_A)  # quarantined later, e.g. by a re-evaluation
    with pytest.raises(pgerr.IntegrityConstraintViolation, match="quarantine"):
        new_version(pg_conn, doc, art, run)


def test_there_is_no_release_or_force_publish_path_in_the_schema(pg_conn: Conn) -> None:
    funcs = {
        r[0]
        for r in pg_conn.execute(
            "SELECT proname FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace WHERE n.nspname = 'public'"
        ).fetchall()
    }
    assert funcs == {"rig_forbid_change", "rig_location_guard", "rig_quarantine_gate", "rig_require_live_run"}
    cols = {
        r[0] for r in pg_conn.execute("SELECT column_name FROM information_schema.columns WHERE table_name='quarantine_record'").fetchall()
    }
    assert not {c for c in cols if "release" in c or "force" in c}


def test_dry_run_runs_can_never_own_source_layer_rows(pg_conn: Conn) -> None:
    dry = new_run(pg_conn, mode="DRY_RUN")
    with pytest.raises(pgerr.IntegrityConstraintViolation, match="LIVE"):
        new_artifact(pg_conn, dry)
    live = new_run(pg_conn)
    art, doc = new_artifact(pg_conn, live), new_doc(pg_conn)
    with pytest.raises(pgerr.IntegrityConstraintViolation, match="LIVE"):
        new_version(pg_conn, doc, art, dry)


# ---- atomicity ---------------------------------------------------------------------------------------------------------------


def test_a_fault_between_statements_leaves_zero_rows(pg_dsn: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with psycopg.connect(pg_dsn, autocommit=True) as conn:
        repo = PgRepository(conn)
        env = Env(repo, tmp_path)
        env.serve("TEST-001", pdf("atomic"))

        def fail_last_statement(*a: object, **k: object) -> None:
            raise RuntimeError("injected fault after artifact+document+version+location were written")

        monkeypatch.setattr(PgRepository, "_insert_result", fail_last_statement)
        with pytest.raises(RuntimeError, match="injected"):
            env.run([one()])
        counts = repo.counts()
        assert {
            k: counts[k] for k in ("raw_artifact", "regulatory_document", "document_version", "document_version_location", "ingest_result")
        } == {"raw_artifact": 0, "regulatory_document": 0, "document_version": 0, "document_version_location": 0, "ingest_result": 0}
        assert conn.execute("SELECT status, abort_reason FROM ingest_run").fetchall() == [
            ("ABORTED", "INTERNAL_ERROR")
        ]  # the run was closed, not left RUNNING
        monkeypatch.undo()
        assert env.run([one()]).results[0].outcome is IngestOutcome.NEW_VERSION  # and the system recovers on the next run


def test_pg_repository_requires_an_autocommit_connection(pg_dsn: str) -> None:
    with psycopg.connect(pg_dsn) as conn, pytest.raises(ValueError, match="autocommit"):
        PgRepository(conn)


# ---- concurrency --------------------------------------------------------------------------------------------------------------


def _ingest_request(repo_run: uuid.UUID, key: str, h: ContentHash, i: int) -> ArtifactIngest:
    cand = candidate(key.split("/")[1], index=i)
    now = T + timedelta(seconds=i)
    return ArtifactIngest(
        repo_run,
        "src",
        "auth",
        cand,
        key,
        h,
        10,
        "application/pdf",
        h.relative_path,
        f"https://h.example/{i}.pdf",
        {},
        now,
        1,
        now,
        uuid.uuid4,
    )


def test_concurrent_writers_of_the_same_content_produce_one_artifact(pg_dsn: str) -> None:
    n = 8
    with psycopg.connect(pg_dsn, autocommit=True) as setup:
        runs = [new_run(setup) for _ in range(n)]
    h = ContentHash(HASH_A)
    barrier, errors, results = threading.Barrier(n), [], []

    def worker(i: int) -> None:
        try:
            with psycopg.connect(pg_dsn, autocommit=True) as conn:
                repo = PgRepository(conn)
                barrier.wait()
                results.append(repo.ingest_artifact(_ingest_request(runs[i], f"src/DOC-{i}", h, i)))
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert errors == [] and len(results) == n
    with psycopg.connect(pg_dsn, autocommit=True) as c:
        assert c.execute("SELECT count(*) FROM raw_artifact").fetchone() == (1,)  # ON CONFLICT: exactly one artifact
        assert c.execute("SELECT count(*), count(DISTINCT document_id) FROM document_version").fetchone() == (
            n,
            n,
        )  # one version per document, never merged
    assert (
        sum(1 for r in results if r.alerts) == n - 1
    )  # every writer except the first to commit its version was told: duplicate bytes under another document


def test_concurrent_writers_of_the_same_document_and_content_produce_one_version(pg_dsn: str) -> None:
    n = 6
    with psycopg.connect(pg_dsn, autocommit=True) as setup:
        runs = [new_run(setup) for _ in range(n)]
    h = ContentHash(HASH_A)
    barrier, errors, results = threading.Barrier(n), [], []

    def worker(i: int) -> None:
        try:
            with psycopg.connect(pg_dsn, autocommit=True) as conn:
                barrier.wait()
                results.append(PgRepository(conn).ingest_artifact(_ingest_request(runs[i], "src/SAME", h, i)))
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert errors == []
    with psycopg.connect(pg_dsn, autocommit=True) as c:
        assert c.execute("SELECT count(*) FROM raw_artifact").fetchone() == (1,)
        assert c.execute("SELECT count(*) FROM regulatory_document").fetchone() == (1,)
        assert c.execute("SELECT count(*) FROM document_version").fetchone() == (1,)
        assert c.execute("SELECT count(*) FROM document_version_location").fetchone() == (n,)  # distinct URLs per writer
    outcomes = sorted(r.outcome.value for r in results)
    assert outcomes.count(IngestOutcome.NEW_VERSION.value) == 1 and set(outcomes) <= {
        "NEW_VERSION",
        "NEW_LOCATION_SAME_VERSION",
        "UNCHANGED_SKIPPED",
    }


def test_concurrent_run_starts_for_one_source_admit_exactly_one(pg_dsn: str) -> None:
    from packages.domain.ingest import IngestRun
    from packages.ingestion.errors import RunAlreadyActive

    with psycopg.connect(pg_dsn, autocommit=True) as setup:
        new_run(setup)  # creates the source row
    n, barrier, outcomes = 6, threading.Barrier(6), []

    def worker() -> None:
        with psycopg.connect(pg_dsn, autocommit=True) as conn:
            run = IngestRun(uuid.uuid4(), "src", RunMode.LIVE, T, "0" * 64, "v", "c", "p", "AUTH")
            barrier.wait()
            try:
                PgRepository(conn).begin_run(run)
                outcomes.append("started")
            except RunAlreadyActive:
                outcomes.append("refused")

    threads = [threading.Thread(target=worker) for _ in range(n)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(outcomes) == ["refused"] * (n - 1) + ["started"]
