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


def make_tree(root: Path, **per_category: str) -> None:
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
