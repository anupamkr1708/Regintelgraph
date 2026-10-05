"""THIRD-PARTY-NOTICES.md must describe exactly what uv.lock resolves (anti-drift). It is an inventory, not legal analysis."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCOPES = {"direct runtime", "direct dev", "transitive runtime", "transitive dev"}


def norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def rows() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    text = (REPO / "THIRD-PARTY-NOTICES.md").read_text(encoding="utf-8")
    in_table = False
    for line in text.splitlines():
        if line.startswith("| Package |"):
            in_table = True
            continue
        if not in_table:
            continue
        if not line.startswith("|"):
            break
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if cells[0].startswith("---"):
            continue
        out[norm(cells[0])] = cells
    return out


def locked() -> dict[str, str]:
    lock = tomllib.loads((REPO / "uv.lock").read_text(encoding="utf-8"))
    return {norm(p["name"]): p["version"] for p in lock["package"] if p.get("source", {}).get("virtual") is None}


def test_the_inventory_lists_exactly_the_locked_packages_at_the_locked_versions() -> None:
    table, lock = rows(), locked()
    assert set(table) == set(lock), set(table) ^ set(lock)
    assert {n: cells[1] for n, cells in table.items()} == lock


def test_every_row_has_a_known_scope_a_licence_evidence_and_a_review_flag() -> None:
    for name, cells in rows().items():
        assert len(cells) == 7, name
        scope, licence, evidence, review = cells[2], cells[3], cells[4], cells[6]
        assert any(scope.startswith(s) for s in SCOPES), (name, scope)
        assert licence and evidence.startswith(("metadata:", "PyPI metadata")), name
        assert review.startswith(("no flag", "**yes**")), name


def test_direct_dependencies_in_pyproject_are_classified_direct() -> None:
    project = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    declared_runtime = {norm(re.split(r"[\[<>=!~ ]", d, maxsplit=1)[0]) for d in project["project"]["dependencies"]}
    declared_dev = {norm(re.split(r"[\[<>=!~ ]", d, maxsplit=1)[0]) for d in project["dependency-groups"]["dev"]}
    table = rows()
    for name in declared_runtime:
        assert table[name][2].startswith("direct runtime"), name
    for name in declared_dev:
        assert table[name][2].startswith("direct dev"), name
    assert table["psycopg-binary"][2].startswith("direct runtime")  # via the declared `psycopg[binary]` extra
    direct = declared_runtime | declared_dev | {"psycopg-binary"}
    assert {n for n, c in table.items() if c[2].startswith("direct")} == direct


def test_the_known_copyleft_family_licences_are_recorded_and_flagged_for_review() -> None:
    table = rows()
    assert table["psycopg"][3] == "LGPL-3.0-only" and table["hypothesis"][3] == "MPL-2.0"
    for name in ("psycopg", "psycopg-binary", "hypothesis", "pathspec"):
        assert table[name][6].startswith("**yes**"), name


def test_the_inventory_states_that_it_is_not_legal_advice() -> None:
    assert "not a legal opinion" in (REPO / "THIRD-PARTY-NOTICES.md").read_text(encoding="utf-8")
