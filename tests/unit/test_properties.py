"""Property-based tests (Hypothesis). Bounded and deterministic: see the `rig` profile in tests/conftest.py."""

from __future__ import annotations

import contextlib
import hashlib
import random
import re
import string
from typing import Any

import yaml
from hypothesis import given
from hypothesis import strategies as st

from packages.domain.content_hash import ContentHash
from packages.domain.ingest import FetchPurpose, IngestOutcome
from packages.ingestion.egress import backoff_ceiling
from packages.ingestion.errors import UrlRejected
from packages.ingestion.logsafe import MAX_LOG_VALUE, neutralize
from packages.ingestion.manifest import parse_manifest
from packages.ingestion.urlpolicy import validate_url
from tests.support.builders import HOST, PREFIXES, pdf, sha
from tests.support.world import World

TAGS = st.text(alphabet=string.ascii_lowercase, min_size=1, max_size=5)
KEYS = st.lists(
    st.text(alphabet=string.ascii_uppercase + string.digits, min_size=1, max_size=6).map(lambda s: f"D-{s}"),
    min_size=1,
    max_size=5,
    unique=True,
)
SAFE_PATH = re.compile(r"^[0-9a-f]{2}/[0-9a-f]{2}/[0-9a-f]{64}$")


@st.composite
def doc_payloads(draw: st.DrawFn) -> dict[str, str]:
    keys = draw(KEYS)
    return {k: draw(TAGS) for k in keys}


# ---- idempotent re-ingestion -----------------------------------------------------------------------------------------------------


@given(doc_payloads())
def test_reingesting_unchanged_content_adds_no_source_layer_rows(payloads: dict[str, str]) -> None:
    world = World()
    world.serve(payloads)
    first = world.run(sorted(payloads))
    before = world.source_layer_counts()
    second = world.run(sorted(payloads))
    assert world.source_layer_counts() == before  # no duplicate logical artifacts, documents, versions or locations
    assert all(r.outcome is IngestOutcome.UNCHANGED_SKIPPED for r in second.results)
    assert [r.content_hash for r in first.results] == [r.content_hash for r in second.results]
    n = len(payloads)
    assert world.repo.counts()["ingest_run"] == 2 and world.repo.counts()["ingest_result"] == 2 * n  # run-scoped bookkeeping may grow


@given(doc_payloads(), st.integers(min_value=2, max_value=4))
def test_idempotence_holds_for_any_number_of_reruns(payloads: dict[str, str], reruns: int) -> None:
    world = World()
    world.serve(payloads)
    world.run(sorted(payloads))
    baseline = world.source_layer_counts()
    for _ in range(reruns):
        world.run(sorted(payloads))
        assert world.source_layer_counts() == baseline


# ---- same bytes -> same hash -> same artifact; different bytes -> different versions ----------------------------------------------------


@given(st.lists(st.sampled_from(["a", "b", "c"]), min_size=1, max_size=6))
def test_same_bytes_give_the_same_content_hash_and_artifact(tags: list[str]) -> None:
    world = World()
    keys = [f"D-{i}" for i in range(len(tags))]
    world.serve(dict(zip(keys, tags, strict=True)))
    report = world.run(keys)
    by_tag: dict[str, set[str | None]] = {}
    for tag, r in zip(tags, report.results, strict=True):
        by_tag.setdefault(tag, set()).add(r.content_hash.value if r.content_hash else None)
    assert all(len(hashes) == 1 for hashes in by_tag.values()) and len({next(iter(h)) for h in by_tag.values()}) == len(by_tag)
    assert world.repo.counts()["raw_artifact"] == len(set(tags)) == len(world.blobs.blobs)  # one artifact and one blob per distinct content
    assert world.repo.counts()["document_version"] == len(tags)  # but a version per document: documents are never merged by content


@given(st.lists(TAGS, min_size=1, max_size=6))
def test_different_bytes_under_one_document_create_different_versions_in_order(tags: list[str]) -> None:
    world = World()
    for tag in tags:
        world.serve({"D-1": tag})
        world.run(["D-1"])
    distinct_in_first_seen_order = list(dict.fromkeys(tags))
    assert world.repo.version_hashes("testreg/D-1") == [sha(pdf(t)) for t in distinct_in_first_seen_order]
    assert world.repo.counts()["document_version"] == len(set(tags))


# ---- hash -> storage path safety ------------------------------------------------------------------------------------------------------


@given(st.binary(max_size=2048))
def test_any_content_hash_yields_a_safe_storage_path(data: bytes) -> None:
    h = ContentHash(hashlib.sha256(data).hexdigest())
    assert SAFE_PATH.fullmatch(h.relative_path) and ".." not in h.relative_path and not h.relative_path.startswith("/")


@given(st.text(max_size=100))
def test_arbitrary_text_is_either_rejected_or_a_safe_path(text: str) -> None:
    try:
        h = ContentHash(text)
    except ValueError:
        return
    assert SAFE_PATH.fullmatch(h.relative_path) and h.value == text


# ---- URL validation invariants ---------------------------------------------------------------------------------------------------------


NASTY = "./\\%@:?#[]{}<>\"' \t\n\r\x00;&=+~*!$,|^`"
url_parts = st.one_of(
    st.text(max_size=60),
    st.builds(
        lambda scheme, userinfo, host, port, path, query, frag: f"{scheme}://{userinfo}{host}{port}{path}{query}{frag}",
        st.sampled_from(["https", "http", "HTTPS", "file", "javascript", "data", "ftp", ""]),
        st.sampled_from(["", "u@", "u:p@", f"{HOST}@"]),
        st.sampled_from(
            [
                HOST,
                HOST.upper(),
                HOST + ".",
                f"x.{HOST}",
                f"{HOST}.evil.example",
                "evil.example",
                "127.0.0.1",
                "[::1]",
                "2130706433",
                "localhost",
            ]
        ),
        st.sampled_from(["", ":443", ":80", ":8443", ":abc", ":"]),
        st.text(alphabet=string.ascii_lowercase + string.digits + NASTY, max_size=40).map(lambda s: "/files/" + s),
        st.sampled_from(["", "?x=1", "?"]),
        st.sampled_from(["", "#frag"]),
    ),
)


@given(url_parts)
def test_validated_urls_always_satisfy_the_safety_invariants(url: str) -> None:
    try:
        v = validate_url(url, FetchPurpose.ATTACHMENT, allowed_hosts=(HOST,), path_prefixes=PREFIXES, max_url_length=200)
    except UrlRejected:
        return  # the ONLY exception type allowed: hostile input must never surface a different error
    assert v.host == HOST and v.port == 443 and v.query == "" and v.url.startswith(f"https://{HOST}/files/")
    assert ".." not in v.path.split("/") and "." not in v.path.split("/")[1:-1] and "//" not in v.path
    assert not any(ord(c) < 0x21 or ord(c) == 0x7F or c == "\\" for c in v.url) and v.url.isascii() and len(v.url) <= 200
    assert "%2f" not in v.path.lower() and "%5c" not in v.path.lower() and "%25" not in v.path


@given(st.text(max_size=300))
def test_url_validation_never_raises_anything_but_url_rejected(url: str) -> None:
    with contextlib.suppress(UrlRejected):
        validate_url(url, FetchPurpose.ATTACHMENT, allowed_hosts=(HOST,), path_prefixes=PREFIXES, max_url_length=200)


# ---- ordering determinism ------------------------------------------------------------------------------------------------------------------


@given(st.permutations(["K-A", "K-B", "K-C", "K-D", "K-E"]))
def test_candidate_order_is_independent_of_manifest_order(order: list[str]) -> None:
    base: dict[str, Any] = {
        "manifest_version": "t",
        "source_id": "testreg",
        "authority": "testreg",
        "domain": {"jurisdiction": "X", "regulator": "Y", "corpus": "Z"},
        "access_review": {"status": "AMBIGUOUS_REQUIRES_REVIEW", "reviewed_by": None, "reviewed_at": None},
        "ingestion_authorized": False,
        "allowed_hosts": [HOST],
        "allowed_url_policy": {
            "schemes": ["https"],
            "allowed_methods": ["GET"],
            "path_prefixes": {"listing": ["/l/"], "detail": ["/d/"], "attachment": ["/f/"]},
        },
        "crawl": {"status": "NOT_CONFIGURED", "user_agent_contact": "env:RIG_C"},
        "document_types": ["circular"],
        "candidate_documents": [{"document_key": k, "tier": "primary", "title": k, "document_type": "circular"} for k in order],
    }
    keys = [c.document_key for c in parse_manifest(yaml.safe_dump(base).encode()).manifest.candidates]
    assert keys == sorted(order)


@given(doc_payloads(), st.randoms(use_true_random=False))
def test_processing_order_and_results_do_not_depend_on_input_order(payloads: dict[str, str], rnd: random.Random) -> None:
    keys = sorted(payloads)
    shuffled = keys[:]
    rnd.shuffle(shuffled)
    a, b = World(), World()
    a.serve(payloads)
    b.serve(payloads)
    ra, rb = a.run(keys), b.run(shuffled)  # manifest construction sorts: both runs must be identical
    assert [r.candidate_key for r in ra.results] == [r.candidate_key for r in rb.results] == [f"testreg/{k}" for k in keys]
    assert [r.content_hash for r in ra.results] == [r.content_hash for r in rb.results]


# ---- small pure helpers --------------------------------------------------------------------------------------------------------------------------


@given(st.integers(1, 40), st.floats(0.1, 10, allow_nan=False), st.floats(0.1, 600, allow_nan=False))
def test_backoff_ceiling_is_monotone_and_capped(attempts: int, base: float, cap: float) -> None:
    ceilings = [backoff_ceiling(a, base, cap) for a in range(1, attempts + 1)]
    assert ceilings == sorted(ceilings) and all(0 < c <= cap for c in ceilings)


@given(st.text(max_size=500), st.lists(st.text(max_size=12), max_size=3))
def test_neutralized_text_is_one_safe_bounded_line_with_no_secrets(text: str, secrets: list[str]) -> None:
    out = neutralize(text, secrets=secrets)
    assert len(out) <= MAX_LOG_VALUE and not any(ord(c) < 0x20 or 0x7F <= ord(c) <= 0x9F or c in "\u2028\u2029" for c in out)
    for secret in (s for s in secrets if len(s) >= 3 and s not in "[REDACTED]…"):
        assert secret not in out or secret in "[REDACTED]\\x" or len(out) == MAX_LOG_VALUE
