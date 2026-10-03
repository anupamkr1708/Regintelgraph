"""Regression: without git (or when git cannot read the tree, e.g. 'dubious ownership'), the repo scanners must still ignore
what .gitignore excludes wholesale (a local .venv, caches, the project-local database cluster) and still flag what would be
committed. Found by running the checks on a freshly unzipped copy of the deliverable."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from scripts import check_foundation, check_secrets

LEAK = "gh" + "p_" + "A" * 36


def make_tree(root: Path) -> None:
    (root / ".gitignore").write_text(".venv/\nlocal_data/\n/logs/\n__pycache__/\n*.egg-info/\n# comment\n!keep/\n")
    for rel, body in {
        ".venv/lib/site.pem": "x",
        ".venv/lib/leak.txt": LEAK,
        "local_data/pg/secret.key": "x",
        "logs/a.log": LEAK,
        "pkg/__pycache__/m.pyc": "x",
        "foo.egg-info/PKG-INFO": LEAK,
        "src/ok.py": "x = 1",
        "docs/readme.md": "hi",
    }.items():
        f = root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(body)


@pytest.fixture
def no_git(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda _name: None)


def names(root: Path, files: list[Path]) -> list[str]:
    return sorted(p.relative_to(root).as_posix() for p in files)


def test_foundation_fallback_honours_gitignore_directories(tmp_path: Path, no_git: None) -> None:
    make_tree(tmp_path)
    assert names(tmp_path, check_foundation._files(tmp_path)) == [".gitignore", "docs/readme.md", "src/ok.py"]


def test_secret_scanner_fallback_honours_gitignore_directories(tmp_path: Path, no_git: None) -> None:
    make_tree(tmp_path)
    assert names(tmp_path, check_secrets.candidate_files(tmp_path)) == [".gitignore", "docs/readme.md", "src/ok.py"]
    assert check_secrets.scan_tree(tmp_path) == []  # the leaks live only in ignored directories


def test_fallback_still_flags_what_would_be_committed(tmp_path: Path, no_git: None) -> None:
    make_tree(tmp_path)
    (tmp_path / "src" / "leak.txt").write_text(LEAK)
    (tmp_path / "creds.pem").write_text("x")
    assert check_secrets.scan_tree(tmp_path) == ["src/leak.txt:1: github-token"]
    assert any("creds.pem" in e for e in check_foundation.check_forbidden_files(tmp_path))


def test_an_unlisted_virtualenv_is_not_hidden(tmp_path: Path, no_git: None) -> None:
    (tmp_path / ".gitignore").write_text("local_data/\n")
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "x.txt").write_text("x")
    assert any(".venv" in e for e in check_foundation.check_forbidden_files(tmp_path))  # not ignored => it WOULD be committed


def test_git_directory_is_never_scanned(tmp_path: Path, no_git: None) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text(LEAK)
    assert check_secrets.scan_tree(tmp_path) == []


def test_fallback_is_deterministic(tmp_path: Path, no_git: None) -> None:
    make_tree(tmp_path)
    assert check_foundation._files(tmp_path) == check_foundation._files(tmp_path)
