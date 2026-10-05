"""CI reproducibility: every container image the workflow uses is pinned by immutable digest, and the pin record agrees (H13/H15)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
WORKFLOWS = sorted((REPO / ".github/workflows").glob("*.y*ml"))
PINNED = re.compile(r"^[a-z0-9][a-z0-9./_-]*@sha256:[0-9a-f]{64}$")
RECORD = REPO / "docs/phase1/local-postgres-plan.md"


def images(workflow: dict[str, Any]) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for job_name, job in workflow["jobs"].items():
        container = job.get("container")
        if container is not None:
            found.append((f"{job_name}.container", container if isinstance(container, str) else container["image"]))
        for svc_name, svc in (job.get("services") or {}).items():
            found.append((f"{job_name}.services.{svc_name}", svc["image"]))
    return found


def test_the_workflow_files_exist() -> None:
    assert WORKFLOWS, "no workflow found - the CI image pin cannot be checked"


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_every_service_and_container_image_is_pinned_by_digest(path: Path) -> None:
    for where, image in images(yaml.safe_load(path.read_text(encoding="utf-8"))):
        assert PINNED.match(image), f"{path.name}: {where} uses a mutable reference: {image}"


def test_the_postgres_service_exists_and_is_the_pgvector_image() -> None:
    wf = yaml.safe_load((REPO / ".github/workflows/foundation-checks.yml").read_text(encoding="utf-8"))
    assert dict(images(wf))["integration.services.postgres"].startswith("pgvector/pgvector@sha256:")


def test_the_pin_record_documents_the_exact_digest_the_workflow_uses() -> None:
    wf = yaml.safe_load((REPO / ".github/workflows/foundation-checks.yml").read_text(encoding="utf-8"))
    image = dict(images(wf))["integration.services.postgres"]
    digest = image.split("@", 1)[1]
    record = RECORD.read_text(encoding="utf-8")
    assert digest in record, "docs/phase1/local-postgres-plan.md must record the digest the workflow pins"
    assert "CI image pin record" in record and "unverified" in record  # the evidence level is stated, not implied


def test_the_checker_rejects_tags_and_short_digests() -> None:
    for bad in ("pgvector/pgvector:pg16", "pgvector/pgvector", "pgvector/pgvector:pg16@sha256:abc", "postgres@sha256:" + "A" * 64):
        assert not PINNED.match(bad), bad
    assert PINNED.match("pgvector/pgvector@sha256:" + "a" * 64)
