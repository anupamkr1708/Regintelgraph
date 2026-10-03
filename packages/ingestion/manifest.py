"""Deterministic manifest reader (docs/phase1/ingestion-contract.md §3.1).

Rules: `yaml.safe_load` only; malformed YAML, duplicate keys, anchors/aliases/merge keys and missing required fields are
rejected; `*_status` labels are preserved exactly and never upgraded; candidates are ordered by `document_key`;
constructing candidates has no side effects. The manifest is configuration/metadata, never raw regulatory content.
"""

from __future__ import annotations

import hashlib
import ipaddress
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml

from packages.domain.content_hash import ContentHash
from packages.domain.manifest import (
    CRAWL_PARAMETER_NAMES,
    AccessReview,
    AccessReviewStatus,
    CrawlConfig,
    DateWindow,
    DomainScope,
    ManifestCandidate,
    PathPrefixes,
    SourceManifest,
    UrlPolicy,
)
from packages.domain.status import StatusLabel
from packages.ingestion.errors import ManifestError

_REQUIRED_TOP = (
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
)
_HOST = re.compile(r"(?=.{1,253}\Z)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+")
_ENV_REF = re.compile(r"env:([A-Z][A-Z0-9_]{0,63})")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_NOT_A_LABEL = frozenset({"resolution_status"})  # `*_status` field that is not a provenance label


@dataclass(frozen=True, slots=True)
class LoadedManifest:
    manifest: SourceManifest
    content_hash: ContentHash  # sha256 of the manifest file bytes; recorded on every IngestRun


def load_manifest(path: Path) -> LoadedManifest:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ManifestError(f"cannot read manifest: {type(exc).__name__}") from exc
    return parse_manifest(raw)


def parse_manifest(raw: bytes) -> LoadedManifest:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ManifestError("manifest is not valid UTF-8") from exc
    _reject_unsafe_yaml_structure(text)
    try:
        data = yaml.safe_load(text)  # the ONLY loader used anywhere (enforced by tests/security/test_yaml_safe_load_only.py)
    except yaml.YAMLError as exc:
        raise ManifestError(f"malformed YAML: {type(exc).__name__}") from exc
    if not isinstance(data, dict):
        raise ManifestError("manifest top level must be a mapping")
    manifest = _build(data)
    return LoadedManifest(manifest=manifest, content_hash=ContentHash(hashlib.sha256(raw).hexdigest()))


# ---- structural YAML guards ---------------------------------------------------------------------------------------


def _reject_unsafe_yaml_structure(text: str) -> None:
    """Reject anchors/aliases (expansion bombs), merge keys and duplicate mapping keys.

    `safe_load` silently lets a later duplicate key override an earlier one, which for a security manifest could flip
    `ingestion_authorized` unnoticed. This pass builds the node graph only; it constructs no Python objects.
    """
    try:
        for event in yaml.parse(text, Loader=yaml.SafeLoader):
            if isinstance(event, yaml.AliasEvent) or getattr(event, "anchor", None) is not None:
                raise ManifestError("YAML anchors and aliases are not allowed in a manifest")
        root = yaml.compose(text, Loader=yaml.SafeLoader)
    except yaml.YAMLError as exc:
        raise ManifestError(f"malformed YAML: {type(exc).__name__}") from exc
    if root is not None:
        _walk_nodes(root)


def _walk_nodes(node: yaml.Node) -> None:
    if isinstance(node, yaml.MappingNode):
        seen: set[str] = set()
        for key_node, value_node in node.value:
            if isinstance(key_node, yaml.ScalarNode):
                if key_node.tag == "tag:yaml.org,2002:merge":
                    raise ManifestError("YAML merge keys are not allowed in a manifest")
                if key_node.value in seen:
                    raise ManifestError(f"duplicate mapping key: {key_node.value!r}")
                seen.add(key_node.value)
            _walk_nodes(value_node)
    elif isinstance(node, yaml.SequenceNode):
        for child in node.value:
            _walk_nodes(child)


# ---- typed field helpers --------------------------------------------------------------------------------------------


def _mapping(value: object, ctx: str) -> Mapping[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(k, str) for k in value):
        raise ManifestError(f"{ctx} must be a mapping with string keys")
    return value


def _string(value: object, ctx: str) -> str:
    if not isinstance(value, str) or not value.strip() or _CONTROL.search(value):
        raise ManifestError(f"{ctx} must be a non-empty string without control characters")
    return value


def _opt_string(data: Mapping[str, Any], key: str, ctx: str) -> str | None:
    value = data.get(key)
    return None if value is None else _string(value, f"{ctx}.{key}")


def _string_list(value: object, ctx: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ManifestError(f"{ctx} must be a list")
    return tuple(_string(v, f"{ctx}[{i}]") for i, v in enumerate(value))


def _iso_date(value: object, ctx: str) -> date:
    if isinstance(value, datetime):
        raise ManifestError(f"{ctx} must be a calendar date, not a timestamp")
    if isinstance(value, date):
        return value
    if isinstance(value, str) and _ISO_DATE.fullmatch(value):
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise ManifestError(f"{ctx} is not a valid date") from exc
    raise ManifestError(f"{ctx} must be an ISO date (YYYY-MM-DD)")


def _opt_review_value(data: Mapping[str, Any], key: str) -> str | None:
    value = data.get(key)
    if value is None:
        return None
    if isinstance(value, datetime | date):
        return value.isoformat()
    return _string(value, f"access_review.{key}")


# ---- builders -----------------------------------------------------------------------------------------------------


def _build(data: Mapping[str, Any]) -> SourceManifest:
    missing = [k for k in _REQUIRED_TOP if k not in data]
    if missing:
        raise ManifestError(f"missing required top-level fields: {', '.join(missing)}")
    if data["ingestion_authorized"] is not True and data["ingestion_authorized"] is not False:
        raise ManifestError("ingestion_authorized must be a literal boolean")
    overrides = data.get("document_overrides")
    if overrides not in (None, []):
        raise ManifestError("document_overrides are not supported until Phase 2 (identity overrides)")

    hosts = _build_hosts(data["allowed_hosts"])
    try:
        return SourceManifest(
            manifest_version=_string(data["manifest_version"], "manifest_version"),
            source_id=_string(data["source_id"], "source_id"),
            authority=_string(data["authority"], "authority"),
            scope=_build_scope(data["domain"]),
            access_review=_build_review(data["access_review"]),
            ingestion_authorized=data["ingestion_authorized"],
            allowed_hosts=hosts,
            url_policy=_build_url_policy(data["allowed_url_policy"], data.get("listing_urls", [])),
            document_types=_string_list(data["document_types"], "document_types"),
            date_window=_build_window(data.get("bounded_date_window")),
            crawl=_build_crawl(data["crawl"]),
            candidates=_build_candidates(data["candidate_documents"]),
        )
    except ValueError as exc:  # domain invariants
        raise ManifestError(str(exc)) from exc


def _build_hosts(value: object) -> tuple[str, ...]:
    hosts = _string_list(value, "allowed_hosts")
    for host in hosts:
        if _HOST.fullmatch(host) is None:
            raise ManifestError(f"allowed_hosts entry must be an exact lowercase hostname (no wildcard, port or path): {host!r}")
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise ManifestError(f"allowed_hosts entry must not be an IP literal: {host!r}")
        if host.rsplit(".", 1)[1].isdigit():
            raise ManifestError(f"allowed_hosts entry looks like a numeric address: {host!r}")
    if len(set(hosts)) != len(hosts):
        raise ManifestError("allowed_hosts contains duplicates")
    return hosts


def _build_scope(value: object) -> DomainScope:
    d = _mapping(value, "domain")
    return DomainScope(
        jurisdiction=_string(d.get("jurisdiction"), "domain.jurisdiction"),
        regulator=_string(d.get("regulator"), "domain.regulator"),
        corpus=_string(d.get("corpus"), "domain.corpus"),
    )


def _build_review(value: object) -> AccessReview:
    d = _mapping(value, "access_review")
    try:
        status = AccessReviewStatus(str(d.get("status")))
    except ValueError as exc:
        raise ManifestError("access_review.status is not a known review status") from exc
    items = d.get("open_items", [])
    return AccessReview(
        status=status,
        reviewed_by=_opt_review_value(d, "reviewed_by"),
        reviewed_at=_opt_review_value(d, "reviewed_at"),
        open_items=_string_list(items, "access_review.open_items") if items else (),
    )


def _build_url_policy(value: object, listing: object) -> UrlPolicy:
    d = _mapping(value, "allowed_url_policy")
    if d.get("schemes") != ["https"]:
        raise ManifestError("allowed_url_policy.schemes must be exactly [https]")
    methods = _string_list(d.get("allowed_methods"), "allowed_url_policy.allowed_methods")
    if not methods or any(m not in ("GET", "HEAD") for m in methods):
        raise ManifestError("allowed_url_policy.allowed_methods must be a non-empty subset of GET, HEAD")
    prefixes = _mapping(d.get("path_prefixes"), "allowed_url_policy.path_prefixes")
    if set(prefixes) != {"listing", "detail", "attachment"}:
        raise ManifestError("path_prefixes must define exactly: listing, detail, attachment")
    if not isinstance(listing, list):
        raise ManifestError("listing_urls must be a list")
    urls: list[str] = []
    for i, entry in enumerate(listing):
        url = _string(_mapping(entry, f"listing_urls[{i}]").get("url"), f"listing_urls[{i}].url")
        if not url.startswith("https://"):
            raise ManifestError(f"listing_urls[{i}].url must be https")
        urls.append(url)
    return UrlPolicy(
        path_prefixes=PathPrefixes(
            listing=_string_list(prefixes["listing"], "path_prefixes.listing"),
            detail=_string_list(prefixes["detail"], "path_prefixes.detail"),
            attachment=_string_list(prefixes["attachment"], "path_prefixes.attachment"),
        ),
        listing_urls=tuple(urls),
    )


def _build_window(value: object) -> DateWindow | None:
    if value is None:
        return None
    d = _mapping(value, "bounded_date_window")
    return DateWindow(start=_iso_date(d.get("from"), "bounded_date_window.from"), end=_iso_date(d.get("to"), "bounded_date_window.to"))


def _build_crawl(value: object) -> CrawlConfig:
    d = _mapping(value, "crawl")
    unknown = set(d) - {"status", "user_agent_contact"} - set(CRAWL_PARAMETER_NAMES)
    if unknown:
        raise ManifestError(f"unknown crawl keys: {', '.join(sorted(unknown))}")
    contact = _string(d.get("user_agent_contact"), "crawl.user_agent_contact")
    ref = _ENV_REF.fullmatch(contact)
    if ref is None:
        raise ManifestError("crawl.user_agent_contact must be `env:<VARIABLE_NAME>` (no contact address in the manifest)")
    params = tuple(sorted((k, d[k]) for k in CRAWL_PARAMETER_NAMES if d.get(k) is not None))
    try:
        return CrawlConfig(status=_string(d.get("status"), "crawl.status"), contact_env_var=ref.group(1), parameters=params)
    except ValueError as exc:
        raise ManifestError(str(exc)) from exc


def _build_candidates(value: object) -> tuple[ManifestCandidate, ...]:
    if not isinstance(value, list):
        raise ManifestError("candidate_documents must be a list")
    out: list[ManifestCandidate] = []
    for index, entry in enumerate(value):
        ctx = f"candidate_documents[{index}]"
        d = _mapping(entry, ctx)
        labels: list[tuple[str, StatusLabel]] = []
        for key in sorted(d):
            if key.endswith("_status") and key not in _NOT_A_LABEL:
                try:
                    labels.append((key, StatusLabel.parse(d[key])))
                except ValueError as exc:
                    raise ManifestError(f"{ctx}.{key} is not a status label") from exc
        listing_date = d.get("listing_date")
        sampled = d.get("sampled", False)
        if not isinstance(sampled, bool):
            raise ManifestError(f"{ctx}.sampled must be a boolean")
        try:
            out.append(
                ManifestCandidate(
                    document_key=_string(d.get("document_key"), f"{ctx}.document_key"),
                    tier=_string(d.get("tier"), f"{ctx}.tier"),
                    title=_string(d.get("title"), f"{ctx}.title"),
                    document_type=_string(d.get("document_type"), f"{ctx}.document_type"),
                    entry_index=index,
                    canonical_url=_opt_string(d, "canonical_url", ctx),
                    document_url=_opt_string(d, "document_url", ctx),
                    source_reference=_opt_string(d, "source_reference", ctx),
                    listing_date=None if listing_date is None else _iso_date(listing_date, f"{ctx}.listing_date"),
                    listing_page_url=_opt_string(d, "listing_page_url", ctx),
                    sampled=sampled,
                    resolution_status=_opt_string(d, "resolution_status", ctx),
                    status_labels=tuple(labels),
                )
            )
        except ValueError as exc:
            raise ManifestError(f"{ctx}: {exc}") from exc
    keys = [c.document_key for c in out]
    if len(set(keys)) != len(keys):
        raise ManifestError("duplicate document_key in candidate_documents")
    return tuple(sorted(out, key=lambda c: c.document_key))
