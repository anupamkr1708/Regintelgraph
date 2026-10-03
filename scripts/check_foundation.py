#!/usr/bin/env python3
"""Mechanical checks for the RegIntelGraph foundation (stdlib only, offline, deterministic).

Provenance: checks encode rules from AGENTS.md (H5, H6, H12), docs/adr/*, and .gitignore policy.
AGENTS.md is the single canonical engineering constitution; this checker inspects it alone.
Usage: python scripts/check_foundation.py [--root PATH]
Exit code 0 = all checks passed, 1 = at least one failure.
"""

from __future__ import annotations

import argparse
import fnmatch
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REQUIRED_FILES: tuple[str, ...] = (
    "AGENTS.md",
    "README.md",
    ".gitignore",
    ".env.example",
    "docs/README.md",
    "docs/executive-summary.md",
    "docs/product-spec.md",
    "docs/architecture.md",
    "docs/domain-model.md",
    "docs/data-model.md",
    "docs/ingestion-design.md",
    "docs/retrieval-design.md",
    "docs/graph-schema.md",
    "docs/temporal-model.md",
    "docs/evidence-model.md",
    "docs/agent-design.md",
    "docs/mcp-design.md",
    "docs/security.md",
    "docs/evaluation.md",
    "docs/observability.md",
    "docs/performance.md",
    "docs/cost-model.md",
    "docs/testing-strategy.md",
    "docs/implementation-roadmap.md",
    "docs/stage-1-review.md",
    "docs/benchmarks/benchmark-spec.md",
    "docs/adr/ADR-001-system-architecture.md",
    "docs/adr/ADR-002-primary-storage.md",
    "docs/adr/ADR-003-retrieval-strategy.md",
    "docs/adr/ADR-004-graph-strategy.md",
    "docs/adr/ADR-005-agent-orchestration.md",
    "docs/adr/ADR-006-model-routing.md",
    "docs/adr/ADR-007-temporal-and-supersession-policy.md",
    "docs/adr/ADR-008-evidence-anchoring-and-citation-handles.md",
    "docs/adr/ADR-009-initial-domain-scope.md",
    # Normative Phase 1 contracts that the ingestion code and its tests cite. Further Phase 1 documents are deliberately
    # NOT enumerated here: an ordinary new phase document must not require an unrelated edit to this checker.
    "docs/phase1/ingestion-contract.md",
    "docs/phase1/source-safety-contract.md",
    "data/manifests/sebi-mutual-funds.yaml",
)
REQUIRED_DIRS: tuple[str, ...] = (
    "apps/api",
    "apps/web",
    "packages/domain",
    "packages/ingestion",
    "packages/retrieval",
    "packages/graph",
    "packages/evidence",
    "packages/agents",
    "packages/evaluation",
    "packages/observability",
    "workers/ingestion",
    "workers/indexing",
    "tests/unit",
    "tests/integration",
    "tests/regression",
    "tests/security",
    "tests/evaluation",
    "data/manifests",
    "data/fixtures",
    "data/eval",
    "scripts",
    "migrations",
    ".github/workflows",
)
ADR_HEADINGS: tuple[str, ...] = (
    "## Context",
    "## Problem",
    "## Decision",
    "## Alternatives considered",
    "## Tradeoffs",
    "## Consequences",
    "## Migration path",
)
ADR_STATUS = re.compile(r"^\*\*Status:\*\*\s+(ACCEPTED|PROPOSED|DEFERRED)\b", re.MULTILINE)
VERIFICATION_STATES: tuple[str, ...] = (
    "SUPPORTED",
    "PARTIALLY_SUPPORTED",
    "CONTRADICTED",
    "INSUFFICIENT_EVIDENCE",
    "TEMPORALLY_AMBIGUOUS",
)
# (probe path, must be ignored?)
GITIGNORE_PROBES: tuple[tuple[str, bool], ...] = (
    (".venv/bin/python", True),
    ("pkg/__pycache__/x.pyc", True),
    (".pytest_cache/x", True),
    (".mypy_cache/x", True),
    (".ruff_cache/x", True),
    ("htmlcov/index.html", True),
    (".coverage", True),
    ("node_modules/a/index.js", True),
    (".next/x", True),
    ("dist/x", True),
    ("coverage/x", True),
    (".env", True),
    (".env.local", True),
    (".vscode/settings.json", True),
    (".idea/x", True),
    (".DS_Store", True),
    ("Thumbs.db", True),
    ("logs/a.txt", True),
    ("run.log", True),
    ("tmp/x", True),
    ("temp/x", True),
    ("artifacts/x", True),
    ("models/w.bin", True),
    ("checkpoints/x", True),
    ("local_data/x", True),
    ("a.db", True),
    ("a.sqlite", True),
    ("a.sqlite3", True),
    (".cache/x", True),
    ("generated/x", True),
    ("build/x", True),
    ("release/x", True),
    ("k.pem", True),
    ("k.key", True),
    ("c.crt", True),
    ("credentials.json", True),
    ("secrets.yaml", True),
    ("data/raw/a.pdf", True),
    ("data/manifests/x.pdf", True),
    ("regintelgraph-stage1-foundation.zip", True),
    # must stay tracked
    (".env.example", False),
    ("data/fixtures/synthetic/doc.pdf", False),
    ("data/manifests/sebi.yaml", False),
    ("data/eval/regintelbench/v0/cases/a.yaml", False),
    ("packages/domain/models/__init__.py", False),
    ("packages/ingestion/logs/parser.py", False),
    ("credentials.example.json", False),
    ("tests/fixtures/x.json", False),
)
SECRET_VALUE = re.compile(r"(sk-[A-Za-z0-9]{10,}|AKIA[0-9A-Z]{12,}|ghp_[A-Za-z0-9]{20,}|xox[baprs]-[A-Za-z0-9-]+)")
SECRET_KEY = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD)", re.IGNORECASE)
LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
FORBIDDEN_NAMES = (".env", ".venv", "node_modules", "venv")
# One canonical constitution (AGENTS.md). A second tool-specific constitution file, or references to one, are drift.
# The tokens are split so that a repo-wide search for stale governance references does not match this checker itself.
_STALE_TOKEN = "CLAU" + "DE"
STALE_GOVERNANCE = re.compile(_STALE_TOKEN + r"\.md|" + _STALE_TOKEN.title() + " Code|" + _STALE_TOKEN)
GOVERNANCE_SECTIONS: tuple[str, ...] = (
    "## Hard rules",
    "## Workflow",
    "## Layering rules",
    "## Dependencies",
    "## Data handling",
    "## Definition of done",
    "## When to stop and ask",
)
TEXT_SUFFIXES = (".md", ".py", ".sh", ".yml", ".yaml", ".toml", ".txt", ".sql", ".cfg", ".ini", ".example")
FORBIDDEN_SUFFIXES = (".pem", ".key", ".crt", ".sqlite", ".sqlite3", ".db")


def _ignored_dir_patterns(root: Path) -> list[str]:
    """Simple directory-name patterns from .gitignore (no negations, no path separators beyond a leading/trailing slash).

    Used ONLY by the no-git fallback so that a local virtualenv, caches or a project-local database cluster are not mistaken
    for repository content. Real git decides when it is available.
    """
    gitignore = root / ".gitignore"
    if not gitignore.is_file():
        return []
    patterns: list[str] = []
    for raw in gitignore.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", "!")):
            continue
        name = line.strip("/")
        if name and "/" not in name:
            patterns.append(name)
    return patterns


def _walk_without_git(root: Path) -> list[Path]:
    ignored = _ignored_dir_patterns(root)
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d != ".git" and not any(fnmatch.fnmatch(d, p) for p in ignored))
        found.extend(Path(dirpath) / f for f in sorted(filenames))
    return sorted(found)


def _files(root: Path) -> list[Path]:
    """Return files that are (or would be) committed: tracked + untracked-not-ignored, excluding .git.

    Inside a git work tree this honours .gitignore, so a local virtualenv, caches or a project-local database cluster
    are not mistaken for repository content. Without git (or if git cannot read the tree) every file is considered EXCEPT
    directories that .gitignore excludes wholesale, so an unpacked archive with a local `.venv` is judged like the repository.
    Provenance: AGENTS.md H12 (what may be committed).
    """
    if shutil.which("git") is not None:
        res = subprocess.run(
            ["git", "-C", str(root), "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            capture_output=True,
            check=False,
        )
        if res.returncode == 0:
            names = sorted({n for n in res.stdout.decode("utf-8", "surrogateescape").split("\0") if n})
            return [root / n for n in names if (root / n).is_file()]
    return _walk_without_git(root)


def check_required(root: Path) -> list[str]:
    """Return failures for missing required files/dirs. Provenance: brief §5 structure + AGENTS.md H5."""
    errs = [f"missing file: {p}" for p in REQUIRED_FILES if not (root / p).is_file()]
    errs += [f"missing dir: {p}" for p in REQUIRED_DIRS if not (root / p).is_dir()]
    errs += [f"empty file: {p}" for p in REQUIRED_FILES if (root / p).is_file() and (root / p).stat().st_size == 0]
    return errs


def check_adrs(root: Path) -> list[str]:
    """Return failures for ADRs lacking required headings/status. Provenance: brief §24."""
    errs: list[str] = []
    for adr in sorted((root / "docs/adr").glob("ADR-*.md")):
        text = adr.read_text(encoding="utf-8")
        if not ADR_STATUS.search(text):
            errs.append(f"{adr.name}: missing/invalid '**Status:** ACCEPTED|PROPOSED|DEFERRED'")
        for h in ADR_HEADINGS:
            if not re.search(rf"^{re.escape(h)}", text, re.MULTILINE):
                errs.append(f"{adr.name}: missing heading '{h}'")
    return errs


def check_links(root: Path) -> list[str]:
    """Return failures for broken relative markdown links. Provenance: docs integrity (anti-drift)."""
    errs: list[str] = []
    for md in (f for f in _files(root) if f.suffix == ".md"):
        for target in LINK.findall(md.read_text(encoding="utf-8")):
            if re.match(r"^(https?:|mailto:|#)", target):
                continue
            path = target.split("#", 1)[0]
            if path and not (md.parent / path).exists():
                errs.append(f"{md.relative_to(root)}: broken link -> {target}")
    return errs


def check_constitution(root: Path) -> list[str]:
    """Validate AGENTS.md, the single canonical constitution: 18 unique H-rules (H1..H18) and required sections.

    Provenance: AGENTS.md hierarchy (safety > constitution > ADRs > implementation); brief §6.
    """
    path = root / "AGENTS.md"
    if not path.is_file():
        return ["AGENTS.md: missing"]
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    rules = [ln for ln in lines if re.match(r"^- \*\*H\d+ ", ln)]
    errs: list[str] = []
    if len(rules) != 18:
        errs.append(f"AGENTS.md: expected 18 hard rules, found {len(rules)}")
    numbers = [int(m.group(1)) for ln in rules if (m := re.match(r"^- \*\*H(\d+) ", ln))]
    duplicated = sorted({n for n in numbers if numbers.count(n) > 1})
    if duplicated:
        errs.append(f"AGENTS.md: duplicated rule ids: {', '.join(f'H{n}' for n in duplicated)}")
    if sorted(set(numbers)) != list(range(1, 19)):
        errs.append("AGENTS.md: rule ids must be exactly H1..H18")
    if len(set(rules)) != len(rules):
        errs.append("AGENTS.md: a hard rule is duplicated verbatim")
    errs += [f"AGENTS.md: missing section '{s}'" for s in GOVERNANCE_SECTIONS if not any(ln.startswith(s) for ln in lines)]
    return errs


def check_governance_references(root: Path) -> list[str]:
    """Reject a second constitution file and any stale reference to one. Provenance: single-constitution decision."""
    errs: list[str] = []
    for f in _files(root):
        rel = f.relative_to(root)
        if rel.name.lower() == (_STALE_TOKEN + ".md").lower():
            errs.append(f"second constitution file present: {rel}")
            continue
        if f.suffix in TEXT_SUFFIXES or f.name in (".gitignore", ".env.example"):
            try:
                text = f.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            for n, line in enumerate(text.splitlines(), 1):
                if STALE_GOVERNANCE.search(line):
                    errs.append(f"{rel}:{n}: stale governance reference")
    return errs


def check_source_gate(root: Path) -> list[str]:
    """Protect the source-access gate invariant using stdlib text checks (no YAML parser in this checker).

    `ingestion_authorized: true` is only consistent when a human review is recorded (reviewer + time) and the review
    status is neither BLOCKED nor AMBIGUOUS_REQUIRES_REVIEW. Provenance: ingestion-contract §0.
    """
    errs: list[str] = []
    for manifest in sorted((root / "data/manifests").glob("*.yaml")):
        text = manifest.read_text(encoding="utf-8")
        rel = manifest.relative_to(root)
        auth = re.search(r"^ingestion_authorized:\s*(\S+)", text, re.MULTILINE)
        if auth is None:
            errs.append(f"{rel}: ingestion_authorized missing (gate must be explicit)")
            continue
        if auth.group(1) not in ("true", "false"):
            errs.append(f"{rel}: ingestion_authorized must be literal true/false")
            continue
        if auth.group(1) == "true":
            status = re.search(r"^\s{2}status:\s*(\S+)", text, re.MULTILINE)
            by = re.search(r"^\s{2}reviewed_by:\s*(\S+)", text, re.MULTILINE)
            at = re.search(r"^\s{2}reviewed_at:\s*(\S+)", text, re.MULTILINE)
            if status is None or status.group(1) in ("BLOCKED", "AMBIGUOUS_REQUIRES_REVIEW"):
                errs.append(f"{rel}: ingestion_authorized is true but access_review.status does not permit it")
            if by is None or by.group(1) == "null" or at is None or at.group(1) == "null":
                errs.append(f"{rel}: ingestion_authorized is true without a recorded reviewer and review time")
    return errs


def check_states(root: Path) -> list[str]:
    """Ensure all five verification states appear in the evidence model and ADR-008. Provenance: brief 2.H."""
    errs: list[str] = []
    for rel in ("docs/evidence-model.md", "docs/adr/ADR-008-evidence-anchoring-and-citation-handles.md"):
        text = (root / rel).read_text(encoding="utf-8")
        errs += [f"{rel}: missing state {s}" for s in VERIFICATION_STATES if s not in text]
    return errs


def check_env_example(root: Path) -> list[str]:
    """Reject secret-looking values in .env.example. Provenance: AGENTS.md H12."""
    errs: list[str] = []
    for n, line in enumerate((root / ".env.example").read_text(encoding="utf-8").splitlines(), 1):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if "=" not in s:
            errs.append(f".env.example:{n}: not KEY=VALUE")
            continue
        key, value = s.split("=", 1)
        if SECRET_VALUE.search(value):
            errs.append(f".env.example:{n}: secret-looking value")
        if SECRET_KEY.search(key) and value.strip():
            errs.append(f".env.example:{n}: non-empty value for secret-like key {key}")
    return errs


def check_forbidden_files(root: Path) -> list[str]:
    """Flag files that must never be committed/zipped. Provenance: AGENTS.md H12, brief §28."""
    errs: list[str] = []
    for p in _files(root):
        rel = p.relative_to(root)
        name = p.name
        if any(part in FORBIDDEN_NAMES for part in rel.parts):
            errs.append(f"forbidden path: {rel}")
        elif name.endswith(FORBIDDEN_SUFFIXES) or re.match(r"^(credentials|secrets)\.(?!example)", name):
            errs.append(f"forbidden file: {rel}")
        if name.endswith(".pdf") and not str(rel).startswith("data/fixtures/"):
            errs.append(f"PDF outside data/fixtures: {rel}")
    return errs


def check_gitignore(root: Path) -> list[str]:
    """Verify .gitignore behaviour with git probes in a temp repo. Provenance: brief §27 (deliberate ignore rules)."""
    if shutil.which("git") is None:
        print("  (skipped: git not available)")
        return []
    errs: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        shutil.copy(root / ".gitignore", Path(tmp) / ".gitignore")
        subprocess.run(["git", "init", "-q"], cwd=tmp, check=True)
        for probe, expect_ignored in GITIGNORE_PROBES:
            res = subprocess.run(["git", "check-ignore", "-q", "--no-index", probe], cwd=tmp)
            ignored = res.returncode == 0
            if ignored != expect_ignored:
                errs.append(f".gitignore: '{probe}' ignored={ignored}, expected {expect_ignored}")
    return errs


def main() -> int:
    """Run all checks and print a summary. Provenance: entry point; no side effects beyond stdout."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=str(Path(__file__).resolve().parent.parent))
    root = Path(parser.parse_args().root).resolve()
    checks = (
        ("required files/dirs", check_required),
        ("ADR structure", check_adrs),
        ("markdown links", check_links),
        ("constitution (AGENTS.md)", check_constitution),
        ("governance references", check_governance_references),
        ("source-access gate", check_source_gate),
        ("verification states", check_states),
        (".env.example", check_env_example),
        ("forbidden files", check_forbidden_files),
        (".gitignore probes", check_gitignore),
    )
    failed = 0
    for name, fn in checks:
        errs = fn(root)
        print(f"[{'FAIL' if errs else ' OK '}] {name}")
        for e in errs:
            print(f"       - {e}")
        failed += bool(errs)
    print("RESULT:", "FAILED" if failed else "all foundation checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
