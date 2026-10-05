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

Pytest controls (docs/testing-strategy.md §4.3): a static count alone does not show that the protected tests RUN, because a
`conftest.py` hook, a collection-ignore setting, a higher-precedence pytest config file, or `__test__ = False` can disable tests
without changing the number of test functions. `pytest_control_violations` therefore also rejects, statically (stdlib `ast` /
`tomllib`; pytest is never imported and no repository code is executed), the known controls that can suppress, deselect, redirect
or short-circuit a run. It is a fail-closed denylist for KNOWN mechanisms, not a proof that every test executes. Legitimate
fixtures, helpers, markers, parametrisation and non-control hooks remain allowed. Any file the guard must read but cannot parse
is a structure error (exit 2), never silently ignored.

Exit codes: 0 ok; 1 ratchet violated; 2 usage/structure error (missing directory, unparsable test/conftest/config file, invalid baseline).
"""

from __future__ import annotations

import argparse
import ast
import configparser
import json
import os
import re
import shlex
import sys
import tomllib
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


# ---- pytest collection / configuration controls ---------------------------------------------------------------------------------

_PRUNED_DIRS = frozenset({".git", ".venv", "venv", "node_modules", "__pycache__", ".mypy_cache", ".ruff_cache", ".pytest_cache", ".tox"})
# pytest picks the first of these it finds, ahead of pyproject.toml, so one of them would silently replace the reviewed config.
_OVERRIDING_CONFIG_NAMES = frozenset({"pytest.ini", ".pytest.ini", "pytest.toml", ".pytest.toml"})
_SHARED_CONFIG_SECTIONS = {"tox.ini": ("pytest",), "setup.cfg": ("tool:pytest", "pytest")}
# Hooks that can remove, skip or re-label items, replace the run loop, or rewrite outcomes/exit status.
_CONTROL_HOOKS = frozenset(
    {
        "pytest_load_initial_conftests",
        "pytest_cmdline_main",
        "pytest_collection",
        "pytest_collection_modifyitems",
        "pytest_ignore_collect",
        "pytest_collect_file",
        "pytest_pycollect_makeitem",
        "pytest_make_collect_report",
        "pytest_deselected",
        "pytest_runtestloop",
        "pytest_runtest_protocol",
        "pytest_runtest_call",
        "pytest_runtest_makereport",
        "pytest_report_teststatus",
        "pytest_sessionfinish",
    }
)
_CONFTEST_IGNORE_NAMES = frozenset({"collect_ignore", "collect_ignore_glob"})
_FORBIDDEN_INI_KEYS = frozenset({"norecursedirs", "python_files", "python_classes", "python_functions", "collect_imported_tests"})
# addopts that select, deselect, ignore, redirect the configuration, load/block plugins, or end a run before it executes tests.
_FORBIDDEN_LONG_OPTS = frozenset(
    {
        "-m", "-k", "-p", "-c", "-o", "-h", "-V",
        "--deselect", "--ignore", "--ignore-glob", "--override-ini", "--rootdir", "--confcutdir", "--noconftest", "--pyargs",
        "--collect-only", "--co", "--setup-plan", "--fixtures", "--fixtures-per-test", "--markers", "--help", "--version",
        "--lf", "--last-failed",
    }
)  # fmt: skip
_FORBIDDEN_SHORT_FLAGS = frozenset("mkpcohV")
_SHORT_FLAGS_TAKING_REST = frozenset("rW")  # `-ra`: the characters after -r are its argument, not more flags
_PLUGIN_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*")
_CI_ENV_CONTROL = re.compile(r"PYTEST_(ADDOPTS|PLUGINS|DISABLE_PLUGIN_AUTOLOAD)")


def _walk(root: Path) -> list[Path]:
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in _PRUNED_DIRS)
        found.extend(Path(dirpath) / name for name in sorted(filenames))
    return found


def _parse_py(path: Path) -> ast.Module:
    try:
        return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError, ValueError, OSError) as exc:
        raise RatchetError(f"cannot parse {path.name} for pytest-control scan: {type(exc).__name__}") from exc


def _parse_toml(path: Path) -> dict[str, Any]:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError, OSError) as exc:
        raise RatchetError(f"cannot parse {path.name} for pytest-control scan: {type(exc).__name__}") from exc


def _target_names(target: ast.expr) -> list[str]:
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, ast.Attribute):
        return [target.attr]
    if isinstance(target, ast.Tuple | ast.List):
        return [name for element in target.elts for name in _target_names(element)]
    if isinstance(target, ast.Starred):
        return _target_names(target.value)
    return []


def _option_chain(node: ast.Attribute) -> bool:
    """True if the attribute chain passes through `.option` (e.g. `config.option.markexpr = ...`)."""
    cur: ast.expr = node
    while isinstance(cur, ast.Attribute):
        if cur.attr == "option":
            return True
        cur = cur.value
    return False


def _assigned(node: ast.AST) -> tuple[list[str], ast.expr | None] | None:
    if isinstance(node, ast.Assign):
        return [n for t in node.targets for n in _target_names(t)], node.value
    if isinstance(node, ast.AnnAssign):
        return _target_names(node.target), node.value
    if isinstance(node, ast.AugAssign):
        return _target_names(node.target), None
    return None


def _dunder_test_findings(tree: ast.Module) -> list[tuple[int, str]]:
    """`__test__ = False` makes pytest skip a module/class/function without lowering the static test count."""
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        assigned = _assigned(node)
        if assigned and "__test__" in assigned[0]:
            value = assigned[1]
            if not (isinstance(value, ast.Constant) and value.value is True):
                out.append((getattr(node, "lineno", 0), "sets __test__ to something other than True (can disable collection)"))
    return out


def _control_findings(tree: ast.Module, *, is_conftest: bool) -> list[tuple[int, str]]:
    out = _dunder_test_findings(tree)
    for node in ast.walk(tree):
        line = getattr(node, "lineno", 0)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name in _CONTROL_HOOKS:
            out.append((line, f"defines pytest control hook '{node.name}'"))
        elif isinstance(node, ast.Import | ast.ImportFrom):
            for alias in node.names:
                for name in (alias.name.split(".")[-1], alias.asname or ""):
                    if name in _CONTROL_HOOKS:
                        out.append((line, f"imports pytest control hook '{name}'"))
        elif isinstance(node, ast.Attribute):
            if node.attr == "pluginmanager":
                out.append((line, "uses pluginmanager (dynamic plugin registration/blocking)"))
            elif isinstance(node.ctx, ast.Store) and _option_chain(node):
                out.append((line, "assigns to config.option (can rewrite selection/ignore options)"))
        assigned = _assigned(node)
        if assigned:
            for name in assigned[0]:
                if name in _CONTROL_HOOKS:
                    out.append((line, f"binds pytest control hook '{name}'"))
                elif is_conftest and name in _CONFTEST_IGNORE_NAMES:
                    out.append((line, f"sets '{name}' (collection ignore)"))
    if is_conftest and _count_skip_markers(tree):
        out.append((0, "uses pytest skip/xfail/importorskip (a conftest-level skip can disable every test beneath it)"))
    return out


def _declared_plugins(tree: ast.Module) -> tuple[list[str], list[int]]:
    """Module-level `pytest_plugins` entries (string literals only) and the lines whose value is not statically resolvable."""
    names: list[str] = []
    unresolved: list[int] = []
    for stmt in tree.body:
        assigned = _assigned(stmt)
        if not assigned or "pytest_plugins" not in assigned[0]:
            continue
        value = assigned[1]
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            names.append(value.value)
        elif isinstance(value, ast.List | ast.Tuple) and all(isinstance(e, ast.Constant) and isinstance(e.value, str) for e in value.elts):
            names.extend(str(e.value) for e in value.elts if isinstance(e, ast.Constant))
        else:
            unresolved.append(stmt.lineno)
    return names, unresolved


def _resolve_plugin(root: Path, name: str) -> Path | None:
    if not _PLUGIN_NAME.fullmatch(name):
        return None
    base = root.joinpath(*name.split("."))
    for candidate in (base.with_suffix(".py"), base / "__init__.py"):
        if candidate.is_file():
            return candidate
    return None


def _scan_module(root: Path, path: Path, *, is_conftest: bool, seen: set[Path], out: list[str]) -> None:
    if path in seen:
        return
    seen.add(path)
    rel = path.relative_to(root).as_posix()
    tree = _parse_py(path)
    for line, message in sorted(_control_findings(tree, is_conftest=is_conftest)):
        out.append(f"pytest-controls: {rel}{f':{line}' if line else ''}: {message}")
    names, unresolved = _declared_plugins(tree)
    out.extend(f"pytest-controls: {rel}:{line}: pytest_plugins is not a literal string/list (cannot be verified)" for line in unresolved)
    for name in names:
        plugin = _resolve_plugin(root, name)
        if plugin is None:
            out.append(f"pytest-controls: {rel}: plugin '{name}' is not a module inside the repository (cannot be verified statically)")
        else:
            _scan_module(root, plugin, is_conftest=False, seen=seen, out=out)  # plugin hooks apply exactly like conftest hooks


def _addopts_tokens(value: object) -> list[str]:
    if isinstance(value, str):
        try:
            return shlex.split(value)
        except ValueError as exc:
            raise RatchetError("pyproject.toml: addopts is not parseable") from exc
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return [str(v) for v in value]
    raise RatchetError("pyproject.toml: addopts must be a string or a list of strings")


def _forbidden_option(token: str) -> bool:
    if token.startswith("--"):
        return token.split("=", 1)[0] in _FORBIDDEN_LONG_OPTS
    if token.startswith("-") and len(token) > 1:
        for char in token[1:]:
            if char in _FORBIDDEN_SHORT_FLAGS:
                return True
            if char in _SHORT_FLAGS_TAKING_REST:
                break
    return False


def _covers(testpath: str, category_rel: str) -> bool:
    tp = testpath.replace("\\", "/").strip("/").removeprefix("./")
    return tp in ("", ".") or category_rel == tp or category_rel.startswith(tp + "/")


def _pyproject_findings(root: Path) -> list[str]:
    path = root / "pyproject.toml"
    if not path.is_file():
        raise RatchetError("pyproject.toml missing: the reviewed pytest configuration cannot be checked")
    tool = _parse_toml(path).get("tool", {})
    pytest_tool = tool.get("pytest") if isinstance(tool, dict) else None
    if not isinstance(pytest_tool, dict):
        raise RatchetError("pyproject.toml has no [tool.pytest...] table: the reviewed pytest configuration is missing")
    ini = pytest_tool.get("ini_options", {})
    if not isinstance(ini, dict):
        raise RatchetError("pyproject.toml: [tool.pytest.ini_options] must be a table")
    merged: dict[str, object] = {k: v for k, v in pytest_tool.items() if k != "ini_options"} | ini  # pytest 9 native + ini-style keys
    out: list[str] = []
    for key in sorted(_FORBIDDEN_INI_KEYS & merged.keys()):
        out.append(f"pytest-controls: pyproject.toml: '{key}' changes what pytest collects (the ratchet assumes pytest's defaults)")
    for token in _addopts_tokens(merged.get("addopts", "")):
        if _forbidden_option(token):
            out.append(f"pytest-controls: pyproject.toml: addopts contains '{token}' (selects/ignores/redirects/short-circuits a run)")
    testpaths = merged.get("testpaths")
    if testpaths is not None:
        paths = [testpaths] if isinstance(testpaths, str) else testpaths
        if not isinstance(paths, list) or not all(isinstance(t, str) for t in paths):
            raise RatchetError("pyproject.toml: testpaths must be a string or a list of strings")
        if any(set(t) & set("*?[") for t in paths):
            out.append("pytest-controls: pyproject.toml: testpaths contains a glob (cannot be verified statically)")
        else:
            for rel in sorted(CATEGORIES.values()):
                if not any(_covers(t, rel) for t in paths):
                    out.append(f"pytest-controls: pyproject.toml: testpaths no longer covers protected directory {rel}")
    return out


def _other_config_findings(root: Path, files: list[Path]) -> list[str]:
    out: list[str] = []
    for path in files:
        rel = path.relative_to(root).as_posix()
        if path.name in _OVERRIDING_CONFIG_NAMES:
            out.append(
                f"pytest-controls: {rel}: pytest config file that takes precedence over pyproject.toml (only pyproject.toml is allowed)"
            )
        elif path.name in _SHARED_CONFIG_SECTIONS:
            parser = configparser.ConfigParser(interpolation=None)
            try:
                parser.read_string(path.read_text(encoding="utf-8"))
            except (configparser.Error, UnicodeDecodeError, OSError) as exc:
                raise RatchetError(f"cannot parse {rel} for pytest-control scan: {type(exc).__name__}") from exc
            if any(parser.has_section(section) for section in _SHARED_CONFIG_SECTIONS[path.name]):
                out.append(f"pytest-controls: {rel}: contains a pytest configuration section (only pyproject.toml is allowed)")
        elif path.name == "pyproject.toml" and path.parent != root:
            tool = _parse_toml(path).get("tool", {})
            if isinstance(tool, dict) and "pytest" in tool:
                out.append(f"pytest-controls: {rel}: contains [tool.pytest] (only the root pyproject.toml is allowed)")
    return out


def _workflow_findings(root: Path, files: list[Path]) -> list[str]:
    out: list[str] = []
    for path in files:
        rel = path.relative_to(root).as_posix()
        if not (rel.startswith(".github/workflows/") and path.suffix in (".yml", ".yaml")):
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (UnicodeDecodeError, OSError) as exc:
            raise RatchetError(f"cannot read {rel} for pytest-control scan: {type(exc).__name__}") from exc
        for number, text in enumerate(lines, start=1):
            if not text.lstrip().startswith("#") and _CI_ENV_CONTROL.search(text):
                out.append(f"pytest-controls: {rel}:{number}: sets a pytest control environment variable")
    return out


def pytest_control_violations(root: Path) -> list[str]:
    """Statically reject known pytest configuration/collection controls that can suppress the protected suites (module docstring)."""
    files = _walk(root)
    out = _pyproject_findings(root)
    out += _other_config_findings(root, files)
    seen: set[Path] = set()
    for path in files:
        if path.name == "conftest.py":
            _scan_module(root, path, is_conftest=True, seen=seen, out=out)
    for rel in sorted(CATEGORIES.values()):
        for test_file in _test_files(root / rel):
            if test_file in seen:
                continue
            rel_file = test_file.relative_to(root).as_posix()
            out += [
                f"pytest-controls: {rel_file}:{line}: {message}" for line, message in sorted(_dunder_test_findings(_parse_py(test_file)))
            ]
    out += _workflow_findings(root, files)
    return sorted(set(out))


# ---- operations ------------------------------------------------------------------------------------------------------------------


def check(root: Path, baseline_path: Path) -> tuple[list[str], list[str]]:
    """(violations, notices)."""
    base = load_baseline(baseline_path)
    tests, skips = measure(root)
    violations: list[str] = []
    notices: list[str] = []
    violations.extend(pytest_control_violations(root))
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
        if any(v.startswith("pytest-controls:") for v in violations):
            print(
                "NOTE: pytest-controls findings are not baseline numbers; remove the control (--update does not clear them)",
                file=sys.stderr,
            )
        print(
            "RESULT: test-count ratchet violated (restore the tests, or --update with a written --reason for human review)", file=sys.stderr
        )
        return 1
    print("RESULT: test-count ratchet holds")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
