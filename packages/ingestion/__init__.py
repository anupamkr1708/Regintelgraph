"""Source ingestion: manifest -> safe fetch -> validate -> fingerprint -> raw storage -> immutable document version.

Implements docs/phase1/ingestion-contract.md and docs/phase1/source-safety-contract.md. Layering: imports `packages.domain`
only. Only `packages/ingestion/net.py` may open sockets (enforced by scripts/check_imports.py and a test).
Live retrieval is gated by the manifest (`ingestion_authorized`); while the gate is closed no network call of any kind
(no DNS, no TCP, no HTTP) is ever attempted.
"""
