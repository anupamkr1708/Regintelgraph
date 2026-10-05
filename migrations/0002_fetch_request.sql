-- 0002_fetch_request — durable audit record of every request attempt (forward-only).
-- Sources: docs/phase1/ingestion-contract.md §3.2 (FetchRequest, trust class RUNTIME) and §9 (deviation #1, now resolved),
-- docs/phase1/source-safety-contract.md §10 (request audit; what is never stored).
-- Records THAT and HOW a request happened, never WHAT came back: no response body, no cookies, no authorization material,
-- no contact identity, no document text. Time is supplied by the application's injected clock (no database default).
-- Deliberately NOT here: parsed content, claims, retrieval, graph (later phases).

CREATE TABLE fetch_request (
    fetch_request_id uuid PRIMARY KEY,
    ingest_run_id    uuid NOT NULL REFERENCES ingest_run (ingest_run_id),
    candidate_key    text NOT NULL CHECK (candidate_key <> ''),
    attempt          integer NOT NULL CHECK (attempt >= 1),
    purpose          text NOT NULL CHECK (purpose IN ('LISTING', 'DETAIL_PAGE', 'ATTACHMENT')),
    requested_url    text NOT NULL CHECK (requested_url <> ''),
    final_url        text,
    redirect_chain   jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(redirect_chain) = 'array'),
    status           integer CHECK (status BETWEEN 100 AND 599),          -- HTTP status of the last response received
    outcome          text NOT NULL CHECK (outcome IN (
                         'OK', 'NOT_MODIFIED', 'URL_REJECTED', 'DNS_REJECTED', 'REDIRECT_REJECTED', 'TLS_ERROR', 'TIMEOUT',
                         'SIZE_EXCEEDED', 'HTTP_PERMANENT', 'HTTP_TRANSIENT', 'RATE_LIMITED_LOCAL', 'CIRCUIT_OPEN')),
    -- Only these response headers may ever be stored (the egress layer's log allowlist). Anything else — Set-Cookie,
    -- Authorization, Proxy-Authenticate, Server, ... — is rejected by the database itself. Widening this list requires a
    -- new reviewed migration; a test pins it to packages.ingestion.logsafe.LOGGED_HEADERS.
    selected_headers jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (
        jsonb_typeof(selected_headers) = 'object'
        AND (selected_headers - ARRAY['content-type', 'content-length', 'content-encoding', 'etag', 'last-modified',
                                      'retry-after', 'location']) = '{}'::jsonb),
    retrieved_at     timestamptz NOT NULL,
    elapsed_seconds  double precision NOT NULL CHECK (elapsed_seconds >= 0 AND elapsed_seconds <> 'NaN' AND elapsed_seconds <> 'Infinity'),
    policy_version   text NOT NULL CHECK (policy_version <> ''),
    content_hash     char(64) CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    trust_class      text NOT NULL DEFAULT 'RUNTIME' CHECK (trust_class = 'RUNTIME'),
    CONSTRAINT fetch_request_ok_means_hashed_200 CHECK (outcome <> 'OK' OR (status = 200 AND content_hash IS NOT NULL)),
    CONSTRAINT fetch_request_not_modified_is_304 CHECK (outcome <> 'NOT_MODIFIED' OR status = 304),
    CONSTRAINT fetch_request_url_rejected_has_no_response CHECK (outcome <> 'URL_REJECTED' OR status IS NULL),
    UNIQUE (ingest_run_id, candidate_key, purpose, attempt)
);

CREATE INDEX fetch_request_run_candidate_idx ON fetch_request (ingest_run_id, candidate_key);

-- Append-only, exactly like the other immutable runtime/audit tables (rig_forbid_change is defined in 0001).
CREATE TRIGGER fetch_request_immutable BEFORE UPDATE OR DELETE ON fetch_request FOR EACH ROW EXECUTE FUNCTION rig_forbid_change();

GRANT SELECT, INSERT ON fetch_request TO rig_app;
