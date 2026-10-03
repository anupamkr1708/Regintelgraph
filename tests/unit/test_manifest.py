from __future__ import annotations

import hashlib
import random
import textwrap
from pathlib import Path

import pytest
import yaml

from packages.domain.manifest import AccessReviewStatus
from packages.domain.status import StatusLabel
from packages.ingestion.errors import ManifestError
from packages.ingestion.manifest import load_manifest, parse_manifest

REPO = Path(__file__).resolve().parents[2]
REAL = REPO / "data/manifests/sebi-mutual-funds.yaml"

MINIMAL = textwrap.dedent(
    """\
    manifest_version: t-1
    source_id: testreg
    authority: testreg
    domain: {jurisdiction: X, regulator: Y, corpus: Z}
    access_review: {status: AMBIGUOUS_REQUIRES_REVIEW, reviewed_by: null, reviewed_at: null}
    ingestion_authorized: false
    allowed_hosts: [docs.testreg.example]
    allowed_url_policy:
      schemes: [https]
      allowed_methods: [GET]
      path_prefixes: {listing: [/list/], detail: [/docs/], attachment: [/files/]}
    crawl: {status: NOT_CONFIGURED, user_agent_contact: "env:RIG_CONTACT"}
    document_types: [circular]
    candidate_documents:
      - {document_key: B-2, tier: primary, title: Second, document_type: circular, document_url_status: OBSERVED, document_url: "https://docs.testreg.example/files/b.pdf"}
      - {document_key: A-1, tier: primary, title: First, document_type: circular, canonical_url_status: VERIFIED, source_reference_status: UNVERIFIED}
    """
)


def test_real_manifest_loads_and_is_fail_closed() -> None:
    loaded = load_manifest(REAL)
    m = loaded.manifest
    assert m.source_id == "sebi-mutual-funds" and len(m.candidates) == 21
    assert m.ingestion_authorized is False and not m.gate_open
    assert m.access_review.status is AccessReviewStatus.AMBIGUOUS_REQUIRES_REVIEW
    assert m.crawl.limits() is None and len(m.crawl.missing_parameters()) == 16  # nothing is configured: fail closed
    assert loaded.content_hash.value == hashlib.sha256(REAL.read_bytes()).hexdigest()


def test_real_manifest_status_labels_are_copied_through_unchanged() -> None:
    """Every `*_status` in the YAML must equal the label the loader exposes (no upgrade, no downgrade, no loss)."""
    raw = yaml.safe_load(REAL.read_text())
    loaded = {c.document_key: c for c in load_manifest(REAL).manifest.candidates}
    checked = 0
    for entry in raw["candidate_documents"]:
        for key, value in entry.items():
            if key.endswith("_status") and key != "resolution_status":
                assert loaded[entry["document_key"]].label(key) is StatusLabel(value)
                checked += 1
    assert checked > 0


def test_real_manifest_candidates_sorted_by_document_key() -> None:
    keys = [c.document_key for c in load_manifest(REAL).manifest.candidates]
    assert keys == sorted(keys) and len(set(keys)) == len(keys)


def test_minimal_manifest_sorts_candidates_and_preserves_labels() -> None:
    m = parse_manifest(MINIMAL.encode()).manifest
    assert [c.document_key for c in m.candidates] == ["A-1", "B-2"]
    a, b = m.candidates
    assert a.label("canonical_url_status") is StatusLabel.VERIFIED and a.label("source_reference_status") is StatusLabel.UNVERIFIED
    assert b.label("document_url_status") is StatusLabel.OBSERVED and b.label("canonical_url_status") is None  # absent stays absent
    assert (a.entry_index, b.entry_index) == (1, 0)  # manifest position kept as provenance, order is by key


def test_ordering_is_independent_of_file_order() -> None:
    raw = yaml.safe_load(MINIMAL)
    orders = []
    for seed in range(5):
        shuffled = list(raw["candidate_documents"])
        random.Random(seed).shuffle(shuffled)
        orders.append(
            [c.document_key for c in parse_manifest(yaml.safe_dump({**raw, "candidate_documents": shuffled}).encode()).manifest.candidates]
        )
    assert all(o == ["A-1", "B-2"] for o in orders)


@pytest.mark.parametrize(
    "field",
    [
        "manifest_version",
        "source_id",
        "authority",
        "domain",
        "access_review",
        "ingestion_authorized",
        "allowed_hosts",
        "allowed_url_policy",
        "crawl",
        "document_types",
        "candidate_documents",
    ],
)
def test_missing_required_top_level_field_is_rejected(field: str) -> None:
    raw = yaml.safe_load(MINIMAL)
    del raw[field]
    with pytest.raises(ManifestError, match="missing required"):
        parse_manifest(yaml.safe_dump(raw).encode())


@pytest.mark.parametrize("text", ["a: [unclosed", "key: : :", "\t- bad", "- just\n- a list", "plain scalar", ""])
def test_malformed_or_non_mapping_yaml_is_rejected(text: str) -> None:
    with pytest.raises(ManifestError):
        parse_manifest(text.encode())


def test_non_utf8_is_rejected() -> None:
    with pytest.raises(ManifestError, match="UTF-8"):
        parse_manifest(b"\xff\xfe\x00bad")


@pytest.mark.parametrize(
    ("mutation", "needle"),
    [
        (lambda t: t.replace("ingestion_authorized: false", "ingestion_authorized: 'false'"), "boolean"),
        (lambda t: t.replace("ingestion_authorized: false", "ingestion_authorized: yes please"), "boolean"),
        (lambda t: t.replace("document_url_status: OBSERVED", "document_url_status: observed"), "status label"),
        (lambda t: t.replace("document_url_status: OBSERVED", "document_url_status: CONFIRMED"), "status label"),
        (lambda t: t.replace("env:RIG_CONTACT", "someone@example.org"), "env:"),
        (lambda t: t.replace("[docs.testreg.example]", "['*.testreg.example']"), "allowed_hosts"),
        (lambda t: t.replace("[docs.testreg.example]", "[127.0.0.1]"), "IP literal"),
        (lambda t: t.replace("[docs.testreg.example]", "['docs.testreg.example:8443']"), "allowed_hosts"),
        (lambda t: t.replace("schemes: [https]", "schemes: [https, http]"), "https"),
        (lambda t: t.replace("document_key: A-1", "document_key: B-2"), "duplicate"),
        (lambda t: t.replace("document_key: A-1", "document_key: '../x'"), "document_key"),
    ],
)
def test_structural_violations_are_rejected(mutation, needle: str) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ManifestError, match=needle):
        parse_manifest(mutation(MINIMAL).encode())


@pytest.mark.parametrize(
    ("text", "needle"),
    [
        ("a: &x 1\nb: *x\n", "anchors"),
        ("base: &b {k: 1}\nother:\n  <<: *b\n", "anchors"),
        ("k: 1\nk: 2\n", "duplicate"),
    ],
)
def test_yaml_anchors_aliases_and_duplicate_keys_are_rejected(text: str, needle: str) -> None:
    with pytest.raises(ManifestError, match=needle):
        parse_manifest(text.encode())


def test_duplicate_authorisation_key_cannot_silently_flip_the_gate() -> None:
    sneaky = MINIMAL.replace("ingestion_authorized: false", "ingestion_authorized: false\ningestion_authorized: true")
    with pytest.raises(ManifestError, match="duplicate"):
        parse_manifest(sneaky.encode())


def test_loading_has_no_side_effects(tmp_path: Path) -> None:
    before = sorted(p.name for p in tmp_path.iterdir())
    a, b = parse_manifest(MINIMAL.encode()), parse_manifest(MINIMAL.encode())
    assert a == b and sorted(p.name for p in tmp_path.iterdir()) == before


def test_missing_file_is_a_manifest_error(tmp_path: Path) -> None:
    with pytest.raises(ManifestError):
        load_manifest(tmp_path / "nope.yaml")
