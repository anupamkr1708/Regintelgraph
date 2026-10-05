#!/usr/bin/env python3
"""Test-count ratchet (docs/testing-strategy.md §4.3). Stdlib only, offline, deterministic. CI job: `foundation`.

Prevents UNREVIEWED decreases in the safety/regression coverage that the governance documents promise to keep: the number of
test functions in the approved categories may not drop below a checked-in baseline, and the number of skip/xfail markers may
not rise above it.

How it counts (static, no pytest run, no database, no third-party code):
  * every `test_*.py` / `*_test.py` file under the category directory, parsed with `ast`;
  * a test is a function whose name starts with `test` (pytest's default prefix) at module level, or inside a `Test*`
    class (how pytest collects). Parametrisation is NOT expanded: one function is one test, so the number does not
    depend on environment, plugins or fixture data (measured equal to pytest's own unique collected functions);
  * tests marked skip/skipif/xfail STILL COUNT (marking a test skipped must not lower the count) and, separately, every skip/xfail
    marker or call (`pytest.mark.skip`, `pytest.skip()`, `pytest.importorskip()`, ...) is counted against a ceiling.

Baseline: `tests/ratchet-baseline.json`. It is only ever changed through `--update`, and lowering a count (or raising the skip
ceiling) requires `--reason` with a written justification, which is appended to the file's history for human review. The file is
also self-validating: the top-level numbers must equal the newest history entry, and every history step that lowers a count
must carry a reason — so a hand edit that silently lowers the baseline fails the check.

Exit codes: 0 ok; 1 ratchet violated; 2 usage/structure error (missing directory, unparsable test file, invalid baseline).
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path
from typing import Any

SCHEMA = 1
BASELINE_RELPATH = "tests/ratchet-baseline.json"
CATEGORIES: dict[str, str] = {
    "integration": "tests/integration",
    "regression": "tests/regression",
    "security": "tests/security",
}
MIN_REASON_CHARS = 20
INITIAL_REASON = "initial baseline derived from the repository's test structure at the time the ratchet was introduced"
_TEST_PREFIX = "test"  # pytest's default `python_functions`; pyproject.toml does not override it
_SKIP_MARKS = frozenset({"skip", "skipif", "xfail"})
_SKIP_CALLS = frozenset({"skip", "xfail", "importorskip"})

Counts = dict[str, int]


class RatchetError(Exception):
    """Structure problem (exit 2), as opposed to a ratchet violation (exit 1)."""


def _is_pytest(node: ast.expr) -> bool:
    return isinstance(node, ast.Name) and node.id == "pytest"


def _count_skip_markers(tree: ast.AST) -> int:
    n = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        if (
            node.attr in _SKIP_MARKS
            and isinstance(node.value, ast.Attribute)
            and node.value.attr == "mark"
            and _is_pytest(node.value.value)
        ):
            n += 1  # pytest.mark.skip / skipif / xfail — as decorator, call or pytestmark value
        elif node.attr in _SKIP_CALLS and _is_pytest(node.value):
            n += 1  # pytest.skip() / xfail() / importorskip()
    return n


def _count_tests(body: list[ast.stmt]) -> int:
    n = 0
    for stmt in body:
        if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef):
            if stmt.name.startswith(_TEST_PREFIX):
                n += 1
        elif isinstance(stmt, ast.ClassDef) and stmt.name.startswith("Test"):
            n += _count_tests(stmt.body)  # methods are only reached through Test* classes, as pytest collects them
    return n


def count_file(path: Path) -> tuple[int, int]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError, ValueError) as exc:
        raise RatchetError(f"cannot parse {path}: {type(exc).__name__}") from exc
    return _count_tests(tree.body), _count_skip_markers(tree)


def _test_files(directory: Path) -> list[Path]:
    found = {*directory.rglob("test_*.py"), *directory.rglob("*_test.py")}
    return sorted(p for p in found if "__pycache__" not in p.parts)


def measure(root: Path) -> tuple[Counts, Counts]:
    """(tests per category, skip markers per category). A missing category directory is an error, never zero."""
    tests: Counts = {}
    skips: Counts = {}
    for category, rel in sorted(CATEGORIES.items()):
        directory = root / rel
        if not directory.is_dir():
            raise RatchetError(f"category directory missing: {rel}")
        t = s = 0
        for file in _test_files(directory):
            ft, fs = count_file(file)
            t += ft
            s += fs
        tests[category], skips[category] = t, s
    return tests, skips


# ---- baseline file ---------------------------------------------------------------------------------------------------------------


def _is_counts(value: object) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == set(CATEGORIES)
        and all(isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in value.values())
    )


def validate_baseline(data: object) -> None:
    """Raises RatchetError unless the baseline is well-formed AND internally consistent (no silent lowering by hand edit)."""
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        raise RatchetError(f"baseline must be an object with schema {SCHEMA}")
    counts, ceilings, history = data.get("counts"), data.get("max_skip_markers"), data.get("history")
    if not _is_counts(counts) or not _is_counts(ceilings):
        raise RatchetError(f"'counts' and 'max_skip_markers' must map exactly {sorted(CATEGORIES)} to non-negative integers")
    if not isinstance(history, list) or not history:
        raise RatchetError("'history' must be a non-empty list")
    prev: dict[str, Any] | None = None
    for i, entry in enumerate(history):
        if not isinstance(entry, dict) or not _is_counts(entry.get("counts")) or not _is_counts(entry.get("max_skip_markers")):
            raise RatchetError(f"history[{i}] is malformed")
        reason = entry.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise RatchetError(f"history[{i}] has no reason")
        if prev is not None:
            lowered = any(entry["counts"][c] < prev["counts"][c] for c in CATEGORIES)
            looser = any(entry["max_skip_markers"][c] > prev["max_skip_markers"][c] for c in CATEGORIES)
            if (lowered or looser) and len(reason.strip()) < MIN_REASON_CHARS:
                raise RatchetError(f"history[{i}] lowers coverage but its reason is shorter than {MIN_REASON_CHARS} characters")
        prev = entry
    assert prev is not None
    if prev["counts"] != counts or prev["max_skip_markers"] != ceilings:
        raise RatchetError("baseline numbers do not match the newest history entry (hand edit without a recorded reason?)")


def load_baseline(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RatchetError(f"baseline missing: {path.name} (create it once with --update)") from exc
    except (OSError, ValueError) as exc:
        raise RatchetError(f"baseline unreadable: {type(exc).__name__}") from exc
    validate_baseline(data)
    assert isinstance(data, dict)
    return data


def _write(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


# ---- operations ------------------------------------------------------------------------------------------------------------------


def check(root: Path, baseline_path: Path) -> tuple[list[str], list[str]]:
    """(violations, notices)."""
    base = load_baseline(baseline_path)
    tests, skips = measure(root)
    violations: list[str] = []
    notices: list[str] = []
    for category in sorted(CATEGORIES):
        floor = base["counts"][category]
        if tests[category] < floor:
            violations.append(f"{category}: {tests[category]} tests, baseline {floor} (dropped by {floor - tests[category]})")
        elif tests[category] > floor:
            notices.append(f"{category}: {tests[category]} tests, baseline {floor} — raise it with --update")
        if skips[category] > base["max_skip_markers"][category]:
            violations.append(f"{category}: {skips[category]} skip/xfail markers, ceiling {base['max_skip_markers'][category]}")
    return violations, notices


def update(root: Path, baseline_path: Path, reason: str | None) -> str:
    """Rewrite the baseline to the current measurement. Lowering coverage requires a written reason."""
    tests, skips = measure(root)
    why = (reason or "").strip()
    if not baseline_path.exists():
        data: dict[str, Any] = {"schema": SCHEMA, "counts": tests, "max_skip_markers": skips, "history": []}
        data["history"].append({"counts": tests, "max_skip_markers": skips, "reason": why or INITIAL_REASON})
        validate_baseline(data)
        _write(baseline_path, data)
        return "baseline created"
    base = load_baseline(baseline_path)
    lowers = [c for c in sorted(CATEGORIES) if tests[c] < base["counts"][c]]
    looser = [c for c in sorted(CATEGORIES) if skips[c] > base["max_skip_markers"][c]]
    if (lowers or looser) and len(why) < MIN_REASON_CHARS:
        raise RatchetError(
            f"refusing to lower the baseline ({', '.join(lowers + looser)}) without --reason of at least {MIN_REASON_CHARS} characters"
        )
    if tests == base["counts"] and skips == base["max_skip_markers"]:
        return "baseline already up to date"
    base["counts"], base["max_skip_markers"] = tests, skips
    base["history"].append({"counts": tests, "max_skip_markers": skips, "reason": why or "baseline raised to the current counts"})
    validate_baseline(base)
    _write(baseline_path, base)
    return "baseline updated"


def main(argv: list[str] | None = None, *, root: Path | None = None) -> int:
    parser = argparse.ArgumentParser(description="Test-count ratchet (see module docstring).")
    parser.add_argument("--update", action="store_true", help="rewrite the baseline to the current counts (explicit, reviewed)")
    parser.add_argument("--reason", help="written justification; REQUIRED when lowering a count or raising the skip ceiling")
    parser.add_argument("--root", type=Path, default=root or Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)
    baseline_path = args.root / BASELINE_RELPATH
    try:
        if args.update:
            print(update(args.root, baseline_path, args.reason))
            return 0
        if args.reason:
            raise RatchetError("--reason is only meaningful with --update")
        violations, notices = check(args.root, baseline_path)
    except RatchetError as exc:
        print(f"RATCHET ERROR: {exc}", file=sys.stderr)
        return 2
    for line in notices:
        print(f"[NOTE] {line}")
    if violations:
        for line in violations:
            print(f"[FAIL] {line}", file=sys.stderr)
        print(
            "RESULT: test-count ratchet violated (restore the tests, or --update with a written --reason for human review)", file=sys.stderr
        )
        return 1
    print("RESULT: test-count ratchet holds")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
