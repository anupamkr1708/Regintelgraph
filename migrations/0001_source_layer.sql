-- 0001_source_layer — Phase 1C source/runtime schema (forward-only).
-- Sources: docs/data-model.md, docs/phase1/ingestion-contract.md §3, §6, §8, docs/phase1/local-postgres-plan.md.
-- Deliberately NOT here: embeddings/pgvector tables or indexes, graph, retrieval, claims, agents, MCP (later phases),
-- and the `authority` table (Phase 2). `authority_id` is a plain slug for now.
-- Time is never defaulted by the database: every instant comes from the application's injected clock.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rig_app') THEN
        CREATE ROLE rig_app NOLOGIN;   -- least-privilege application role; the schema owner runs migrations
    END IF;
END
$$;

CREATE TABLE source (
    source_id    text PRIMARY KEY CHECK (source_id ~ '^[a-z0-9][a-z0-9_-]{0,63}$'),
    authority_id text NOT NULL CHECK (authority_id ~ '^[a-z0-9][a-z0-9_-]{0,63}$'),
    manifest_ref text NOT NULL,
    enabled      boolean NOT NULL,
    created_at   timestamptz NOT NULL
);

CREATE TABLE ingest_run (
    ingest_run_id         uuid PRIMARY KEY,
    source_id             text NOT NULL REFERENCES source (source_id),
    mode                  text NOT NULL CHECK (mode IN ('DRY_RUN', 'LIVE')),
    status                text NOT NULL CHECK (status IN ('RUNNING', 'COMPLETED', 'COMPLETED_WITH_FAILURES', 'ABORTED')),
    abort_reason          text CHECK (abort_reason IN ('GATE_CLOSED', 'POLICY_VIOLATION', 'CIRCUIT_OPEN', 'OPERATOR_CANCEL', 'INTERNAL_ERROR')),
    started_at            timestamptz NOT NULL,
    finished_at           timestamptz,
    manifest_hash         char(64) NOT NULL CHECK (manifest_hash ~ '^[0-9a-f]{64}$'),
    manifest_version      text NOT NULL,
    code_version          text NOT NULL,
    safety_policy_version text NOT NULL,
    authorisation_ref     text,
    stats                 jsonb NOT NULL DEFAULT '{}'::jsonb,
    CHECK ((status = 'ABORTED') = (abort_reason IS NOT NULL)),
    CHECK ((status = 'RUNNING') = (finished_at IS NULL)),
    CHECK (finished_at IS NULL OR finished_at >= started_at),
    -- a LIVE run that actually ran must carry the human authorisation reference
    CHECK (mode <> 'LIVE' OR status = 'ABORTED' OR authorisation_ref IS NOT NULL)
);
-- At most one RUNNING run per source: two concurrent runs for the same source are prevented by the database.
CREATE UNIQUE INDEX ingest_run_one_running_per_source ON ingest_run (source_id) WHERE status = 'RUNNING';

CREATE TABLE raw_artifact (
    raw_artifact_id    uuid PRIMARY KEY,
    content_hash       char(64) NOT NULL UNIQUE CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    size_bytes         bigint NOT NULL CHECK (size_bytes >= 0),
    media_type_sniffed text NOT NULL,
    -- the storage path is a pure function of the hash (never of a URL or filename)
    blob_path_rel      text NOT NULL CHECK (blob_path_rel = substr(content_hash, 1, 2) || '/' || substr(content_hash, 3, 2) || '/' || content_hash),
    trust_class        text NOT NULL DEFAULT 'SOURCE' CHECK (trust_class = 'SOURCE'),
    ingest_run_id      uuid NOT NULL REFERENCES ingest_run (ingest_run_id),  -- the run that first stored these bytes
    created_at         timestamptz NOT NULL,
    UNIQUE (raw_artifact_id, content_hash)
);

CREATE TABLE regulatory_document (
    document_id      uuid PRIMARY KEY,
    source_id        text NOT NULL REFERENCES source (source_id),
    authority_id     text NOT NULL,
    document_type    text NOT NULL,
    source_reference text,                    -- manifest claim (UNTRUSTED); Phase 2 resolves identity properly
    title            text NOT NULL,
    identity_key     text NOT NULL UNIQUE,    -- manifest-keyed: '<source_id>/<document_key>'
    created_at       timestamptz NOT NULL
);

CREATE TABLE document_version (
    document_version_id    uuid PRIMARY KEY,
    document_id            uuid NOT NULL REFERENCES regulatory_document (document_id),
    raw_artifact_id        uuid NOT NULL,
    content_hash           char(64) NOT NULL,
    retrieved_at           timestamptz NOT NULL,
    ingest_run_id          uuid NOT NULL REFERENCES ingest_run (ingest_run_id),   -- -> manifest hash/version, code & policy version
    manifest_entry_index   integer NOT NULL CHECK (manifest_entry_index >= 0),
    manifest_status_labels jsonb NOT NULL,                                          -- copied through unchanged, never upgraded
    status                 text NOT NULL CHECK (status = 'STORED'),                 -- PUBLISHED needs parse/index (later phase)
    trust_class            text NOT NULL DEFAULT 'SOURCE' CHECK (trust_class = 'SOURCE'),
    UNIQUE (document_id, content_hash),
    FOREIGN KEY (raw_artifact_id, content_hash) REFERENCES raw_artifact (raw_artifact_id, content_hash)
);
CREATE INDEX document_version_content_hash_idx ON document_version (content_hash);

CREATE TABLE document_version_location (
    document_version_id uuid NOT NULL REFERENCES document_version (document_version_id),
    canonical_url       text NOT NULL,
    first_seen          timestamptz NOT NULL,
    last_seen           timestamptz NOT NULL,   -- the ONLY mutable source-layer field
    http_meta           jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (document_version_id, canonical_url),
    CHECK (last_seen >= first_seen)
);

CREATE TABLE quarantine_record (
    quarantine_id         uuid PRIMARY KEY,
    ingest_run_id         uuid NOT NULL REFERENCES ingest_run (ingest_run_id),
    candidate_key         text NOT NULL,
    reason_code           text NOT NULL CHECK (reason_code IN (
        'HOST_NOT_ALLOWED', 'REDIRECT_OFF_ALLOWLIST', 'SCHEME_NOT_HTTPS', 'PRIVATE_OR_RESERVED_IP', 'CONTENT_TYPE_MISMATCH',
        'MAGIC_BYTES_MISMATCH', 'SIZE_EXCEEDED', 'PDF_SANITY_FAILED', 'AUTHORITY_MISMATCH', 'VIEWER_WRAPPER_URL', 'MANIFEST_SCOPE_MISMATCH')),
    content_hash          char(64) CHECK (content_hash ~ '^[0-9a-f]{64}$'),   -- present only when the full body was received
    bytes_stored          boolean NOT NULL,
    requested_url         text NOT NULL,
    final_url             text,
    redirect_chain        jsonb NOT NULL DEFAULT '[]'::jsonb,
    http_status           integer,
    headers               jsonb NOT NULL DEFAULT '{}'::jsonb,                  -- allowlisted headers only
    sniffed_media_type    text,
    reason_detail         text NOT NULL,                                       -- sanitised, bounded; never a body excerpt
    safety_policy_version text NOT NULL,
    created_at            timestamptz NOT NULL,
    CHECK (bytes_stored = (content_hash IS NOT NULL)),
    UNIQUE NULLS NOT DISTINCT (ingest_run_id, candidate_key, reason_code, content_hash)
);

CREATE TABLE ingest_result (
    ingest_run_id         uuid NOT NULL REFERENCES ingest_run (ingest_run_id),
    candidate_key         text NOT NULL,
    outcome               text NOT NULL CHECK (outcome IN (
        'NEW_VERSION', 'NEW_LOCATION_SAME_VERSION', 'UNCHANGED_SKIPPED', 'QUARANTINED', 'FAILED_TRANSIENT', 'FAILED_PERMANENT',
        'UNRESOLVED', 'OUT_OF_SCOPE', 'GATE_CLOSED')),
    attempts              integer NOT NULL CHECK (attempts >= 0),
    reason_code           text,
    document_version_id   uuid REFERENCES document_version (document_version_id),
    content_hash          char(64) CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    previous_content_hash char(64) CHECK (previous_content_hash ~ '^[0-9a-f]{64}$'),
    quarantine_id         uuid REFERENCES quarantine_record (quarantine_id),
    alerts                text[] NOT NULL DEFAULT '{}',
    created_at            timestamptz NOT NULL,
    PRIMARY KEY (ingest_run_id, candidate_key),
    CHECK (outcome <> 'QUARANTINED' OR quarantine_id IS NOT NULL),
    CHECK (outcome NOT IN ('NEW_VERSION', 'NEW_LOCATION_SAME_VERSION', 'UNCHANGED_SKIPPED') OR document_version_id IS NOT NULL)
);

CREATE TABLE job (
    job_id          uuid PRIMARY KEY,
    kind            text NOT NULL,
    payload         jsonb NOT NULL,
    idempotency_key text NOT NULL UNIQUE,
    state           text NOT NULL CHECK (state IN ('QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED')),
    attempts        integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    max_attempts    integer NOT NULL CHECK (max_attempts >= 1),
    run_after       timestamptz NOT NULL,
    locked_by       text,
    locked_at       timestamptz,
    last_error      text,
    created_at      timestamptz NOT NULL,
    updated_at      timestamptz NOT NULL,
    CHECK ((state = 'RUNNING') = (locked_by IS NOT NULL))
);
CREATE INDEX job_claim_idx ON job (run_after, created_at, job_id) WHERE state = 'QUEUED';

CREATE TABLE job_event (
    event_id   bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    job_id     uuid NOT NULL REFERENCES job (job_id),
    event_type text NOT NULL CHECK (event_type IN ('ENQUEUED', 'CLAIMED', 'SUCCEEDED', 'RETRY_SCHEDULED', 'FAILED')),
    at         timestamptz NOT NULL,
    detail     text
);

-- ---------------------------------------------------------------------------------------------------------------------
-- Immutability (source layer is append-only). Raised even for the schema owner; the app role additionally lacks the privilege.
CREATE FUNCTION rig_forbid_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'table % is append-only (% not allowed)', TG_TABLE_NAME, TG_OP USING ERRCODE = 'integrity_constraint_violation';
END
$$;

CREATE TRIGGER raw_artifact_immutable BEFORE UPDATE OR DELETE ON raw_artifact FOR EACH ROW EXECUTE FUNCTION rig_forbid_change();
CREATE TRIGGER regulatory_document_immutable BEFORE UPDATE OR DELETE ON regulatory_document FOR EACH ROW EXECUTE FUNCTION rig_forbid_change();
CREATE TRIGGER document_version_immutable BEFORE UPDATE OR DELETE ON document_version FOR EACH ROW EXECUTE FUNCTION rig_forbid_change();
CREATE TRIGGER quarantine_record_immutable BEFORE UPDATE OR DELETE ON quarantine_record FOR EACH ROW EXECUTE FUNCTION rig_forbid_change();
CREATE TRIGGER ingest_result_immutable BEFORE UPDATE OR DELETE ON ingest_result FOR EACH ROW EXECUTE FUNCTION rig_forbid_change();
CREATE TRIGGER job_event_immutable BEFORE UPDATE OR DELETE ON job_event FOR EACH ROW EXECUTE FUNCTION rig_forbid_change();

-- document_version_location: only `last_seen` may change, and only forward in time. No deletes.
CREATE FUNCTION rig_location_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'document_version_location rows are never deleted' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF (NEW.document_version_id, NEW.canonical_url, NEW.first_seen, NEW.http_meta)
        IS DISTINCT FROM (OLD.document_version_id, OLD.canonical_url, OLD.first_seen, OLD.http_meta) THEN
        RAISE EXCEPTION 'only document_version_location.last_seen is mutable' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.last_seen < OLD.last_seen THEN
        RAISE EXCEPTION 'last_seen may only move forward' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER document_version_location_guard BEFORE UPDATE OR DELETE ON document_version_location FOR EACH ROW EXECUTE FUNCTION rig_location_guard();

-- Quarantine gate: content that has a quarantine record can never become retrievable source content.
-- (No release path exists in Phase 1C; a reviewed release is a later, explicit migration.)
CREATE FUNCTION rig_quarantine_gate() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF EXISTS (SELECT 1 FROM quarantine_record q WHERE q.content_hash = NEW.content_hash) THEN
        RAISE EXCEPTION 'content % has a quarantine record and cannot be published', NEW.content_hash USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER raw_artifact_quarantine_gate BEFORE INSERT ON raw_artifact FOR EACH ROW EXECUTE FUNCTION rig_quarantine_gate();
CREATE TRIGGER document_version_quarantine_gate BEFORE INSERT ON document_version FOR EACH ROW EXECUTE FUNCTION rig_quarantine_gate();

-- Synthetic dry-run content can never enter the source layer: the owning run must be LIVE.
CREATE FUNCTION rig_require_live_run() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM ingest_run r WHERE r.ingest_run_id = NEW.ingest_run_id AND r.mode = 'LIVE') THEN
        RAISE EXCEPTION 'source-layer rows require a LIVE ingest_run' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER raw_artifact_live_only BEFORE INSERT ON raw_artifact FOR EACH ROW EXECUTE FUNCTION rig_require_live_run();
CREATE TRIGGER document_version_live_only BEFORE INSERT ON document_version FOR EACH ROW EXECUTE FUNCTION rig_require_live_run();

-- ---------------------------------------------------------------------------------------------------------------------
-- Least privilege for the application role: append-only tables get INSERT/SELECT; mutable runtime tables get UPDATE.
GRANT SELECT, INSERT ON source, raw_artifact, regulatory_document, document_version, document_version_location,
    quarantine_record, ingest_result, job_event TO rig_app;
GRANT SELECT, INSERT, UPDATE ON ingest_run, job TO rig_app;
GRANT UPDATE (last_seen) ON document_version_location TO rig_app;
