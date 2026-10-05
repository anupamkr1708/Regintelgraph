"""The test-count ratchet itself (scripts/check_test_ratchet.py): counting rules, ratchet direction, reviewed updates, self-validation."""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import check_test_ratchet as r

REPO = Path(__file__).resolve().parents[2]


PYPROJECT = (
    '[tool.pytest.ini_options]\nminversion = "8.0"\ntestpaths = ["tests"]\npythonpath = ["."]\n'
    'addopts = "-ra --strict-markers --strict-config --import-mode=importlib"\nmarkers = ["postgres: needs PostgreSQL"]\n'
)


def make_tree(root: Path, **per_category: str) -> None:
    (root / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")  # the guard requires the reviewed pytest config (fail closed)
    for category, rel in r.CATEGORIES.items():
        d = root / rel
        d.mkdir(parents=True, exist_ok=True)
        (d / "test_x.py").write_text(per_category.get(category, ""), encoding="utf-8")


def make_src(n: int) -> str:
    return "".join(f"def test_{i}():\n    pass\n\n" for i in range(n))


def run(root: Path, *args: str) -> int:
    return r.main([*args], root=root)


# ---- counting -------------------------------------------------------------------------------------------------------------------


def test_counts_module_functions_async_and_test_class_methods(tmp_path: Path) -> None:
    make_tree(
        tmp_path,
        security=(
            "def test_a(): pass\n"
            "async def test_b(): pass\n"
            "def helper(): pass\n"
            "def testcase_without_underscore(): pass\n"
            "class TestG:\n    def test_c(self): pass\n    def helper(self): pass\n    class TestInner:\n        def test_d(self): pass\n"
            "class Helper:\n    def test_not_collected(self): pass\n"
        ),
    )
    tests, _ = r.measure(tmp_path)
    assert tests["security"] == 5  # a, b, testcase_without_underscore, TestG.test_c, TestG.TestInner.test_d (pytest's `test` prefix)


def test_nested_functions_named_test_are_not_counted(tmp_path: Path) -> None:
    make_tree(tmp_path, regression="def test_outer():\n    def test_inner(): pass\n    return test_inner\n")
    assert r.measure(tmp_path)[0]["regression"] == 1


def test_only_test_files_are_counted_and_nested_directories_are_included(tmp_path: Path) -> None:
    make_tree(tmp_path, integration=make_src(2))
    (tmp_path / "tests/integration/helpers.py").write_text(make_src(5), encoding="utf-8")  # not a test file
    sub = tmp_path / "tests/integration/sub"
    sub.mkdir()
    (sub / "test_nested.py").write_text(make_src(3), encoding="utf-8")
    (sub / "other_test.py").write_text(make_src(1), encoding="utf-8")
    assert r.measure(tmp_path)[0]["integration"] == 6


def test_skipped_and_xfailed_tests_still_count_and_markers_are_counted_separately(tmp_path: Path) -> None:
    make_tree(
        tmp_path,
        security=(
            "import pytest\n"
            "@pytest.mark.skip(reason='x')\ndef test_a(): pass\n"
            "@pytest.mark.skipif(True, reason='x')\ndef test_b(): pass\n"
            "@pytest.mark.xfail\ndef test_c(): pass\n"
            "def test_d():\n    pytest.skip('now')\n"
            "def test_e():\n    pytest.importorskip('x')\n"
            "pytestmark = pytest.mark.skip\n"
        ),
    )
    tests, skips = r.measure(tmp_path)
    assert tests["security"] == 5 and skips["security"] == 6


def test_counting_is_deterministic(tmp_path: Path) -> None:
    make_tree(tmp_path, security=make_src(7), regression=make_src(2), integration=make_src(3))
    assert r.measure(tmp_path) == r.measure(tmp_path)


def test_a_missing_category_directory_is_an_error_not_zero(tmp_path: Path) -> None:
    make_tree(tmp_path)
    (tmp_path / "tests/regression/test_x.py").unlink()
    (tmp_path / "tests/regression").rmdir()
    with pytest.raises(r.RatchetError, match="missing"):
        r.measure(tmp_path)


def test_an_unparsable_test_file_is_an_error_not_a_silent_zero(tmp_path: Path) -> None:
    make_tree(tmp_path, security="def test_a(:\n")
    with pytest.raises(r.RatchetError, match="cannot parse"):
        r.measure(tmp_path)


# ---- ratchet direction -----------------------------------------------------------------------------------------------------------


def baseline_tree(tmp_path: Path, sec: int = 3, reg: int = 2, integ: int = 4) -> Path:
    make_tree(tmp_path, security=make_src(sec), regression=make_src(reg), integration=make_src(integ))
    assert run(tmp_path, "--update") == 0
    return tmp_path


def test_unchanged_counts_pass(tmp_path: Path) -> None:
    assert run(baseline_tree(tmp_path)) == 0


def test_a_decrease_in_any_category_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = baseline_tree(tmp_path)
    (root / "tests/regression/test_x.py").write_text(make_src(1), encoding="utf-8")
    assert run(root) == 1
    assert "regression: 1 tests, baseline 2" in capsys.readouterr().err


def test_deleting_a_test_file_fails(tmp_path: Path) -> None:
    root = baseline_tree(tmp_path)
    (root / "tests/security/test_x.py").unlink()
    assert run(root) == 1


def test_an_increase_passes_with_a_notice_to_raise_the_baseline(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = baseline_tree(tmp_path)
    (root / "tests/security/test_x.py").write_text(make_src(5), encoding="utf-8")
    assert run(root) == 0
    assert "raise it with --update" in capsys.readouterr().out


def test_new_skip_markers_beyond_the_ceiling_fail_even_if_the_count_holds(tmp_path: Path) -> None:
    root = baseline_tree(tmp_path)
    (root / "tests/security/test_x.py").write_text(
        "import pytest\n@pytest.mark.skip\ndef test_0(): pass\n" + make_src(3)[len("def test_0():\n    pass\n\n") :], encoding="utf-8"
    )
    assert r.measure(root)[0]["security"] == 3
    assert run(root) == 1


def test_moving_tests_between_files_does_not_trip_the_ratchet(tmp_path: Path) -> None:
    root = baseline_tree(tmp_path)
    (root / "tests/security/test_x.py").write_text(make_src(1), encoding="utf-8")
    (root / "tests/security/test_y.py").write_text("".join(f"def test_m{i}(): pass\n" for i in range(2)), encoding="utf-8")
    assert run(root) == 0


# ---- reviewed updates ------------------------------------------------------------------------------------------------------------


def test_update_to_a_lower_count_is_refused_without_a_written_reason(tmp_path: Path) -> None:
    root = baseline_tree(tmp_path)
    before = (root / r.BASELINE_RELPATH).read_text()
    (root / "tests/security/test_x.py").write_text(make_src(1), encoding="utf-8")
    assert run(root, "--update") == 2
    assert run(root, "--update", "--reason", "too short") == 2
    assert (root / r.BASELINE_RELPATH).read_text() == before  # nothing written


def test_update_to_a_lower_count_with_a_reason_is_recorded_in_history(tmp_path: Path) -> None:
    root = baseline_tree(tmp_path)
    (root / "tests/security/test_x.py").write_text(make_src(1), encoding="utf-8")
    reason = "two tests merged into one parametrised test, reviewed in change X"
    assert run(root, "--update", "--reason", reason) == 0
    data = json.loads((root / r.BASELINE_RELPATH).read_text())
    assert data["counts"]["security"] == 1 and data["history"][-1]["reason"] == reason and len(data["history"]) == 2
    assert run(root) == 0


def test_raising_the_baseline_needs_no_reason_and_never_lowers_anything(tmp_path: Path) -> None:
    root = baseline_tree(tmp_path)
    (root / "tests/security/test_x.py").write_text(make_src(9), encoding="utf-8")
    assert run(root, "--update") == 0
    assert json.loads((root / r.BASELINE_RELPATH).read_text())["counts"]["security"] == 9


def test_update_when_nothing_changed_writes_nothing(tmp_path: Path) -> None:
    root = baseline_tree(tmp_path)
    before = (root / r.BASELINE_RELPATH).read_text()
    assert run(root, "--update") == 0 and (root / r.BASELINE_RELPATH).read_text() == before


def test_raising_the_skip_ceiling_needs_a_reason(tmp_path: Path) -> None:
    root = baseline_tree(tmp_path)
    (root / "tests/security/test_x.py").write_text(
        "import pytest\n@pytest.mark.xfail\ndef test_0(): pass\n" + make_src(3)[len("def test_0():\n    pass\n\n") :], encoding="utf-8"
    )
    assert run(root, "--update") == 2


def test_reason_without_update_is_rejected(tmp_path: Path) -> None:
    assert run(baseline_tree(tmp_path), "--reason", "whatever reason text here") == 2


# ---- the baseline file validates itself ------------------------------------------------------------------------------------------


def test_a_hand_edited_lower_baseline_without_history_fails(tmp_path: Path) -> None:
    root = baseline_tree(tmp_path)
    path = root / r.BASELINE_RELPATH
    data = json.loads(path.read_text())
    data["counts"]["security"] -= 1  # silently lower, then delete tests to match
    path.write_text(json.dumps(data))
    assert run(root) == 2


def test_a_lowering_history_entry_must_carry_a_real_reason() -> None:
    good = {"schema": 1, "counts": dict.fromkeys(r.CATEGORIES, 5), "max_skip_markers": dict.fromkeys(r.CATEGORIES, 0), "history": []}
    lowered = {"counts": dict.fromkeys(r.CATEGORIES, 4), "max_skip_markers": dict.fromkeys(r.CATEGORIES, 0)}
    good["history"] = [
        {**{"counts": dict.fromkeys(r.CATEGORIES, 5), "max_skip_markers": dict.fromkeys(r.CATEGORIES, 0)}, "reason": INITIAL},
        {**lowered, "reason": "ok"},
    ]  # type: ignore[dict-item]
    bad = copy.deepcopy(good)
    bad["counts"] = dict.fromkeys(r.CATEGORIES, 4)
    with pytest.raises(r.RatchetError, match="reason is shorter"):
        r.validate_baseline(bad)


INITIAL = "initial baseline for this unit test"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.update(schema=2),
        lambda d: d["counts"].pop("security"),
        lambda d: d["counts"].update(extra=1),
        lambda d: d["counts"].update(security=-1),
        lambda d: d["counts"].update(security=True),
        lambda d: d["max_skip_markers"].update(security="0"),
        lambda d: d.update(history=[]),
        lambda d: d["history"][0].update(reason=""),
    ],
)
def test_malformed_baselines_are_rejected(tmp_path: Path, mutate: object) -> None:
    root = baseline_tree(tmp_path)
    data = json.loads((root / r.BASELINE_RELPATH).read_text())
    mutate(data)  # type: ignore[operator]
    with pytest.raises(r.RatchetError):
        r.validate_baseline(data)


def test_a_missing_baseline_fails_the_check_and_is_created_only_by_update(tmp_path: Path) -> None:
    make_tree(tmp_path, security=make_src(1))
    assert run(tmp_path) == 2
    assert run(tmp_path, "--update") == 0 and run(tmp_path) == 0


# ---- the real repository ---------------------------------------------------------------------------------------------------------


def test_the_checked_in_baseline_is_valid_and_the_repository_holds_it() -> None:
    violations, _ = r.check(REPO, REPO / r.BASELINE_RELPATH)
    assert violations == []


def test_the_script_runs_standalone_with_only_the_standard_library() -> None:
    proc = subprocess.run(
        [sys.executable, "-S", str(REPO / "scripts/check_test_ratchet.py")], capture_output=True, text=True, cwd=REPO, check=False
    )
    assert proc.returncode == 0 and "holds" in proc.stdout, proc.stderr


# ---- pytest collection/configuration controls (P1 review finding on PR #1) -----------------------------------------------------


def put(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def with_pyproject(root: Path, ini_body: str) -> None:
    put(root, "pyproject.toml", f"[tool.pytest.ini_options]\n{ini_body}\n")


def controls(root: Path) -> list[str]:
    return r.pytest_control_violations(root)


def protected_tree(tmp_path: Path) -> Path:
    """A valid ratcheted tree: baseline recorded, counts hold, no controls."""
    root = baseline_tree(tmp_path)
    assert run(root) == 0
    assert controls(root) == []
    return root


# Case 1 — the review finding: valid test files + a conftest collection hook; the static count does not move.


@pytest.mark.parametrize("hook", sorted(r._CONTROL_HOOKS))
def test_a_conftest_control_hook_is_rejected_while_the_static_count_is_unchanged(tmp_path: Path, hook: str) -> None:
    root = protected_tree(tmp_path)
    before = r.measure(root)
    put(root, "tests/security/conftest.py", f"def {hook}(*args, **kwargs):\n    return None\n")
    assert r.measure(root) == before  # the count-only ratchet would still pass: exactly the reported gap
    assert any(f"control hook '{hook}'" in v for v in controls(root))
    assert run(root) == 1


def test_the_reported_bypass_shape_is_rejected_end_to_end(tmp_path: Path) -> None:
    root = protected_tree(tmp_path)
    put(
        root,
        "tests/conftest.py",
        "def pytest_collection_modifyitems(config, items):\n    items[:] = [i for i in items if 'security' not in str(i.path)]\n",
    )
    violations, _ = r.check(root, root / r.BASELINE_RELPATH)
    assert any("tests/conftest.py:1" in v and "pytest_collection_modifyitems" in v for v in violations)
    assert run(root) == 1


@pytest.mark.parametrize(
    "source",
    [
        "async def pytest_ignore_collect(collection_path): return True\n",
        "from helpers import pytest_collection_modifyitems\n",
        "from helpers import hook as pytest_ignore_collect\n",
        "pytest_collection_modifyitems = lambda config, items: items.clear()\n",
        "def f(config):\n    config.pluginmanager.set_blocked('x')\n",
        "def f(config):\n    config.option.markexpr = 'nothing'\n",
        "import pytest\n\n@pytest.fixture(autouse=True)\ndef _off():\n    pytest.skip('x')\n",
    ],
)
def test_other_ways_to_install_a_control_in_a_conftest_are_rejected(tmp_path: Path, source: str) -> None:
    root = protected_tree(tmp_path)
    put(root, "tests/integration/conftest.py", source)
    assert controls(root) and run(root) == 1


def test_a_plugin_named_in_pytest_plugins_is_scanned_like_a_conftest(tmp_path: Path) -> None:
    root = protected_tree(tmp_path)
    put(root, "tests/conftest.py", 'pytest_plugins = ["tests.support.plug"]\n')
    put(root, "tests/support/__init__.py", "")
    put(root, "tests/support/plug.py", "def pytest_runtest_makereport(item, call):\n    pass\n")
    assert any("tests/support/plug.py" in v and "pytest_runtest_makereport" in v for v in controls(root))


def test_dunder_test_false_disables_a_module_or_class_without_changing_the_count(tmp_path: Path) -> None:
    root = protected_tree(tmp_path)
    before = r.measure(root)
    put(root, "tests/security/test_x.py", "__test__ = False\n" + (root / "tests/security/test_x.py").read_text(encoding="utf-8"))
    put(root, "tests/regression/test_x.py", "class TestG:\n    __test__ = False\n    def test_a(self): pass\n" + make_src(1))
    assert r.measure(root)[0]["security"] == before[0]["security"]
    found = controls(root)
    assert any("tests/security/test_x.py:1" in v and "__test__" in v for v in found)
    assert any("tests/regression/test_x.py:2" in v for v in found)


# Case 2 — collection-ignore and selection configuration.


@pytest.mark.parametrize("name", ["collect_ignore", "collect_ignore_glob"])
def test_a_conftest_collection_ignore_is_rejected(tmp_path: Path, name: str) -> None:
    root = protected_tree(tmp_path)
    put(root, "tests/conftest.py", f'{name} = ["security"]\n')
    assert any(f"'{name}'" in v for v in controls(root))
    assert run(root) == 1


@pytest.mark.parametrize(
    "ini",
    [
        'norecursedirs = ["security"]',
        'python_files = ["check_*.py"]',
        'python_classes = ["Check"]',
        'python_functions = ["check"]',
        "collect_imported_tests = false",
        "addopts = \"-ra -m 'not security'\"",
        'addopts = "-ra -k nothing"',
        'addopts = ["--deselect", "tests/security/test_x.py::test_0"]',
        'addopts = "--ignore=tests/security"',
        'addopts = "--ignore-glob=tests/*/test_x.py"',
        'addopts = "-p no:cacheprovider"',
        'addopts = "-c other.ini"',
        'addopts = "-o testpaths=x"',
        'addopts = "--collect-only"',
        'addopts = "--setup-plan"',
        'addopts = "--lf"',
        'addopts = "--noconftest"',
        'addopts = "-xk nothing"',
        'addopts = "-qm nothing"',
        'testpaths = ["tests/unit"]',
        'testpaths = ["tests/*"]',
    ],
)
def test_pyproject_selection_and_ignore_settings_are_rejected(tmp_path: Path, ini: str) -> None:
    root = protected_tree(tmp_path)
    with_pyproject(root, ini)
    assert controls(root), ini
    assert run(root) == 1


def test_native_pytest_toml_table_in_pyproject_is_checked_too(tmp_path: Path) -> None:
    root = protected_tree(tmp_path)
    put(root, "pyproject.toml", '[tool.pytest]\nnorecursedirs = ["security"]\n')
    assert any("'norecursedirs'" in v for v in controls(root))


@pytest.mark.parametrize("name", ["pytest.ini", ".pytest.ini", "pytest.toml", ".pytest.toml"])
@pytest.mark.parametrize("where", ["", "tests/", "tests/security/"])
def test_a_higher_precedence_pytest_config_file_anywhere_is_rejected(tmp_path: Path, name: str, where: str) -> None:
    root = protected_tree(tmp_path)
    put(root, f"{where}{name}", "[pytest]\n")
    assert any(f"{where}{name}" in v and "precedence" in v for v in controls(root))
    assert run(root) == 1


@pytest.mark.parametrize(("name", "content"), [("tox.ini", "[pytest]\naddopts = -ra\n"), ("setup.cfg", "[tool:pytest]\naddopts = -ra\n")])
def test_shared_config_files_with_a_pytest_section_are_rejected(tmp_path: Path, name: str, content: str) -> None:
    root = protected_tree(tmp_path)
    put(root, name, content)
    assert any(name in v for v in controls(root))


def test_a_pytest_table_in_a_non_root_pyproject_is_rejected(tmp_path: Path) -> None:
    root = protected_tree(tmp_path)
    put(root, "tests/security/pyproject.toml", "[tool.pytest.ini_options]\naddopts = ''\n")
    assert any("tests/security/pyproject.toml" in v for v in controls(root))


def test_ci_environment_pytest_controls_are_rejected_but_comments_are_not(tmp_path: Path) -> None:
    root = protected_tree(tmp_path)
    put(root, ".github/workflows/ci.yml", "# PYTEST_ADDOPTS is documented here\njobs:\n  t:\n    env:\n      PYTEST_ADDOPTS: -m nothing\n")
    found = controls(root)
    assert len(found) == 1 and ".github/workflows/ci.yml:5" in found[0]


# Case 3 — legitimate structure and configuration still pass.


def test_legitimate_fixtures_helpers_markers_hooks_and_config_still_pass(tmp_path: Path) -> None:
    root = protected_tree(tmp_path)
    put(
        root,
        "tests/conftest.py",
        "import pytest\n\npytest_plugins = ['tests.support.plug']\n\n"
        "def pytest_configure(config):\n    config.addinivalue_line('markers', 'slow: slow')\n\n"
        "def pytest_generate_tests(metafunc):\n    pass\n\n"
        "@pytest.fixture(autouse=True)\ndef _env(monkeypatch):\n    monkeypatch.setenv('X', '1')\n\n"
        "@pytest.fixture(params=['a', pytest.param('b', marks=pytest.mark.postgres)])\ndef kind(request):\n    return request.param\n",
    )
    put(root, "tests/support/__init__.py", "")
    put(
        root,
        "tests/support/plug.py",
        "import pytest\n\n@pytest.fixture\ndef db():\n    pytest.skip('reviewed, env-guarded runtime skip in a plugin fixture')\n",
    )
    put(root, "tests/security/test_x.py", "__test__ = True\n" + (root / "tests/security/test_x.py").read_text(encoding="utf-8"))
    put(root, "tox.ini", "[flake8]\nmax-line-length = 100\n")
    put(root, "setup.cfg", "[metadata]\nname = x\n")
    put(root, ".github/workflows/ci.yml", "jobs:\n  t:\n    steps:\n      - run: pytest -m 'not postgres' -ra\n")
    with_pyproject(
        root,
        'testpaths = ["tests"]\naddopts = "-ra -rfEsxXpP -q -x --strict-markers --strict-config --import-mode=importlib"\nmarkers = ["a: b"]',
    )
    assert controls(root) == []
    assert run(root) == 0


def test_testpaths_that_cover_the_protected_directories_pass(tmp_path: Path) -> None:
    root = protected_tree(tmp_path)
    with_pyproject(root, 'testpaths = ["tests/integration", "./tests/regression/", "tests/security"]')
    assert controls(root) == []
    with_pyproject(root, 'testpaths = ["."]')
    assert controls(root) == []


def test_the_real_repository_conftests_plugins_and_config_pass() -> None:
    assert r.pytest_control_violations(REPO) == []


# Case 4 — moving tests between files/directories cannot hide a control.


def test_moving_tests_between_files_does_not_hide_a_control(tmp_path: Path) -> None:
    root = protected_tree(tmp_path)
    before_tests, _ = r.measure(root)
    put(root, "tests/security/deep/er/test_moved.py", (root / "tests/security/test_x.py").read_text(encoding="utf-8"))
    (root / "tests/security/test_x.py").unlink()
    assert r.measure(root)[0] == before_tests  # count preserved by the move
    put(root, "tests/security/deep/conftest.py", "def pytest_ignore_collect(collection_path):\n    return True\n")
    assert any("tests/security/deep/conftest.py" in v for v in controls(root))
    put(root, "conftest.py", "collect_ignore_glob = ['tests/*']\n")  # repository-root conftest
    assert any(v.startswith("pytest-controls: conftest.py") for v in controls(root))


def test_a_conftest_outside_the_protected_directories_is_still_scanned(tmp_path: Path) -> None:
    root = protected_tree(tmp_path)
    put(root, "tests/unit/conftest.py", "def pytest_collection_modifyitems(items):\n    items.clear()\n")
    assert any("tests/unit/conftest.py" in v for v in controls(root))


def test_pruned_vendor_directories_are_not_scanned(tmp_path: Path) -> None:
    root = protected_tree(tmp_path)
    put(root, ".venv/lib/site-packages/pkg/conftest.py", "def pytest_collection_modifyitems(items):\n    items.clear()\n")
    put(root, ".venv/lib/site-packages/pkg/setup.cfg", "[tool:pytest]\n")
    assert controls(root) == []


# Case 5 — unreadable/malformed configuration fails closed (structure error, exit 2), never silently ignored.


@pytest.mark.parametrize(
    ("rel", "text"),
    [
        ("tests/conftest.py", "def broken(:\n"),
        ("tests/integration/conftest.py", "x = (\n"),
        ("pyproject.toml", "[tool.pytest.ini_options\n"),
        ("pyproject.toml", "[tool.ruff]\nline-length = 1\n"),  # no pytest table at all
        ("pyproject.toml", "[tool.pytest.ini_options]\naddopts = 5\n"),
        ("pyproject.toml", '[tool.pytest.ini_options]\naddopts = "-ra \'unbalanced"\n'),
        ("pyproject.toml", "[tool.pytest.ini_options]\ntestpaths = 5\n"),
        ("tox.ini", "[pytest\n"),
    ],
)
def test_unparsable_or_missing_pytest_configuration_is_a_structure_error(tmp_path: Path, rel: str, text: str) -> None:
    root = protected_tree(tmp_path)
    put(root, rel, text)
    with pytest.raises(r.RatchetError):
        controls(root)
    assert run(root) == 2


def test_a_missing_root_pyproject_is_a_structure_error(tmp_path: Path) -> None:
    root = protected_tree(tmp_path)
    (root / "pyproject.toml").unlink()
    assert run(root) == 2


@pytest.mark.parametrize("value", ["plugins_from_somewhere()", "[name for name in x]", "['ok', other]"])
def test_a_non_literal_pytest_plugins_value_is_rejected(tmp_path: Path, value: str) -> None:
    root = protected_tree(tmp_path)
    put(root, "tests/conftest.py", f"pytest_plugins = {value}\n")
    assert any("not a literal" in v for v in controls(root))


@pytest.mark.parametrize("name", ["some_third_party_plugin", "tests.support.missing", "../escape"])
def test_a_plugin_that_cannot_be_resolved_inside_the_repository_is_rejected(tmp_path: Path, name: str) -> None:
    root = protected_tree(tmp_path)
    put(root, "tests/conftest.py", f"pytest_plugins = [{name!r}]\n")
    assert any("cannot be verified" in v for v in controls(root))


def test_a_plugin_cycle_terminates(tmp_path: Path) -> None:
    root = protected_tree(tmp_path)
    put(root, "tests/conftest.py", "pytest_plugins = ['tests.a']\n")
    put(root, "tests/a.py", "pytest_plugins = ['tests.b']\n")
    put(root, "tests/b.py", "pytest_plugins = ['tests.a']\n")
    assert controls(root) == []


def test_the_baseline_file_is_untouched_by_the_controls_guard(tmp_path: Path) -> None:
    root = protected_tree(tmp_path)
    before = (root / r.BASELINE_RELPATH).read_bytes()
    put(root, "tests/conftest.py", "collect_ignore = ['security']\n")
    assert run(root) == 1
    assert (root / r.BASELINE_RELPATH).read_bytes() == before
