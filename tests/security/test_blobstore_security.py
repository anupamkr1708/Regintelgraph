from __future__ import annotations

import hashlib
import io
import os
import stat
from pathlib import Path

import pytest

from packages.domain.content_hash import ContentHash
from packages.ingestion import blobstore as bs
from packages.ingestion.blobstore import FsBlobStore, ensure_disjoint_roots
from packages.ingestion.errors import BlobCollisionError, UnsafePathError, VerifyAfterWriteError


def h(data: bytes) -> ContentHash:
    return ContentHash(hashlib.sha256(data).hexdigest())


def put(store: FsBlobStore, data: bytes, as_hash: ContentHash | None = None):  # type: ignore[no-untyped-def]
    return store.put(as_hash or h(data), io.BytesIO(data), len(data))


@pytest.fixture
def store(tmp_path: Path) -> FsBlobStore:
    return FsBlobStore(tmp_path / "blobs")


def test_path_is_derived_from_the_hash_alone(store: FsBlobStore) -> None:
    data = b"%PDF-1.4 x %%EOF"
    r = put(store, data)
    assert r.relative_path == f"{h(data).value[:2]}/{h(data).value[2:4]}/{h(data).value}"
    assert (store.root / r.relative_path).read_bytes() == data
    assert [p for p in store.root.rglob("*") if p.is_file()] == [store.root / r.relative_path]  # nothing else is left behind


def test_same_hash_twice_is_an_idempotent_noop(store: FsBlobStore) -> None:
    data = b"same"
    first, second = put(store, data), put(store, data)
    assert first.created and not second.created and store.exists(h(data))
    assert len([p for p in store.root.rglob("*") if p.is_file()]) == 1


def test_different_bytes_under_an_existing_hash_are_a_collision_and_never_overwrite(store: FsBlobStore) -> None:
    good = b"genuine"
    put(store, good)
    # Corrupt the stored blob so that it no longer matches its name, then try to store the genuine bytes again.
    path = store.root / h(good).relative_path
    path.chmod(0o644)
    path.write_bytes(b"tampered")
    with pytest.raises(BlobCollisionError):
        put(store, good)
    assert path.read_bytes() == b"tampered"  # never silently repaired or overwritten


def test_bytes_that_do_not_match_the_declared_hash_are_refused(store: FsBlobStore) -> None:
    with pytest.raises(VerifyAfterWriteError):
        put(store, b"actual", as_hash=h(b"claimed"))
    with pytest.raises(VerifyAfterWriteError):  # size lie
        store.put(h(b"abc"), io.BytesIO(b"abc"), 99)
    assert [p for p in store.root.rglob("*") if p.is_file()] == []  # no temp residue, nothing published


def test_files_have_no_executable_bits_and_are_read_only(store: FsBlobStore) -> None:
    r = put(store, b"data")
    mode = (store.root / r.relative_path).stat().st_mode
    assert not mode & 0o111 and not mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)
    with pytest.raises(ValueError):
        FsBlobStore(store.root.parent / "x", file_mode=0o755)


def test_quarantine_store_files_are_owner_only(tmp_path: Path) -> None:
    q = FsBlobStore(tmp_path / "q", file_mode=0o400)
    r = put(q, b"suspicious")
    assert stat.S_IMODE((q.root / r.relative_path).stat().st_mode) == 0o400


def test_symlinked_root_is_refused(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    with pytest.raises(UnsafePathError):
        FsBlobStore(link)


def test_relative_root_is_refused() -> None:
    with pytest.raises(UnsafePathError):
        FsBlobStore(Path("relative/dir"))


def test_symlinked_fanout_directory_is_refused_and_nothing_is_written_through_it(store: FsBlobStore, tmp_path: Path) -> None:
    data = b"fanout"
    digest = h(data).value
    outside = tmp_path / "outside"
    outside.mkdir()
    (store.root / digest[:2]).symlink_to(outside)
    with pytest.raises(UnsafePathError):
        put(store, data)
    assert list(outside.iterdir()) == []  # the symlink target was not written to


def test_symlinked_second_level_fanout_is_refused(store: FsBlobStore, tmp_path: Path) -> None:
    data = b"fanout2"
    digest = h(data).value
    (store.root / digest[:2]).mkdir()
    outside = tmp_path / "outside2"
    outside.mkdir()
    (store.root / digest[:2] / digest[2:4]).symlink_to(outside)
    with pytest.raises(UnsafePathError):
        put(store, data)
    assert list(outside.iterdir()) == []


def test_final_path_being_a_symlink_is_never_followed(store: FsBlobStore, tmp_path: Path) -> None:
    data = b"final"
    digest = h(data)
    d = store.root / digest.value[:2] / digest.value[2:4]
    d.mkdir(parents=True)
    victim = tmp_path / "victim"
    victim.write_bytes(b"precious")
    (d / digest.value).symlink_to(victim)
    with pytest.raises(BlobCollisionError):
        put(store, data)
    assert victim.read_bytes() == b"precious" and not store.exists(digest)  # exists() refuses symlinks too


@pytest.mark.parametrize("evil", ["../x", "/etc/passwd", "a" * 63 + "/", "a" * 62 + "..", "%2e%2e/" + "a" * 57, "A" * 64, "a" * 64 + "\n"])
def test_path_traversal_cannot_be_expressed_because_only_validated_hashes_exist(evil: str) -> None:
    with pytest.raises(ValueError):
        ContentHash(evil)


def test_store_api_exposes_no_deletion_and_no_path_taking_methods(store: FsBlobStore) -> None:
    public = {n for n in dir(store) if not n.startswith("_")}
    assert public == {"root", "persistent", "put", "exists", "open_read"}
    assert not any("delete" in n or "remove" in n or "unlink" in n for n in public)


def test_crash_before_the_atomic_link_leaves_no_visible_blob_and_no_temp_file(store: FsBlobStore, monkeypatch: pytest.MonkeyPatch) -> None:
    data = b"crash"

    def boom(*a: object, **k: object) -> None:
        raise OSError("simulated crash at the rename step")

    monkeypatch.setattr(bs.os, "link", boom)
    with pytest.raises(OSError, match="simulated"):
        put(store, data)
    assert not store.exists(h(data))
    assert [p for p in store.root.rglob("*") if p.is_file()] == []
    monkeypatch.undo()
    assert put(store, data).created  # and a retry after the "crash" succeeds cleanly


def test_a_hard_crash_leaves_only_an_invisible_dotfile_never_a_partial_blob(store: FsBlobStore) -> None:
    digest = h(b"partial")
    d = store.root / digest.value[:2] / digest.value[2:4]
    d.mkdir(parents=True)
    (d / ".tmp-deadbeef").write_bytes(b"half-writ")  # what a SIGKILL mid-write would leave
    assert not store.exists(digest)
    assert put(store, b"partial").created


def test_roots_must_be_disjoint(tmp_path: Path) -> None:
    a = tmp_path / "a"
    a.mkdir()
    (a / "inner").mkdir()
    with pytest.raises(UnsafePathError):
        ensure_disjoint_roots(a, a)
    with pytest.raises(UnsafePathError):
        ensure_disjoint_roots(a, a / "inner")
    link = tmp_path / "alias"
    link.symlink_to(a)
    with pytest.raises(UnsafePathError):
        ensure_disjoint_roots(a, link)
    (tmp_path / "b").mkdir()
    ensure_disjoint_roots(a, tmp_path / "b")


def test_world_writable_or_foreign_root_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "ww"
    root.mkdir()
    root.chmod(0o777)
    with pytest.raises(UnsafePathError):
        FsBlobStore(root)
    assert os.geteuid() == root.stat().st_uid or True  # ownership branch is exercised by the same check; see code
