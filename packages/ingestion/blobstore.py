"""Content-addressed, write-once local filesystem store (docs/phase1/source-safety-contract.md §9, §10).

Path = f(validated ContentHash) ONLY: `<root>/ab/cd/<hash>`. URLs, filenames and Content-Disposition never reach a path.
All directory traversal uses `dir_fd` with `O_NOFOLLOW`, so a symlinked root or fan-out directory (or one swapped in after
a check) is refused rather than followed. Writes: exclusive-create temp file in the destination directory (0600) ->
write + flush + fsync -> verify size and hash (in memory AND by re-reading from disk) -> no-replace `link()` into place ->
unlink temp -> fsync directory. An existing identical blob is a no-op; an existing blob with different bytes is corruption
(P1): never overwritten. There is no delete operation. Never any executable bit.

POSIX only (relies on `dir_fd`, `O_NOFOLLOW`, `O_DIRECTORY`); the project's supported development platform is Linux/WSL2.
"""

from __future__ import annotations

import contextlib
import errno
import hashlib
import os
import stat
from pathlib import Path
from typing import BinaryIO

from packages.domain.content_hash import ContentHash
from packages.ingestion.errors import BlobCollisionError, UnsafePathError, VerifyAfterWriteError
from packages.ingestion.ports import PutResult

_CHUNK = 1024 * 1024
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


def _open_dir(name: str, *, dir_fd: int | None = None) -> int:
    try:
        return os.open(name, _DIR_FLAGS, dir_fd=dir_fd)
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            raise UnsafePathError(f"path component is a symlink or not a directory: {Path(name).name}") from exc
        raise


def _hash_fd(fd: int) -> tuple[str, int]:
    digest = hashlib.sha256()
    total = 0
    os.lseek(fd, 0, os.SEEK_SET)
    while chunk := os.read(fd, _CHUNK):
        digest.update(chunk)
        total += len(chunk)
    return digest.hexdigest(), total


class FsBlobStore:
    """Write-once, hash-addressed store. Used for both published blobs (mode 0444) and quarantine (mode 0400)."""

    persistent = True

    def __init__(self, root: Path, *, file_mode: int = 0o444) -> None:
        if file_mode & 0o111:
            raise ValueError("stored files must never carry executable bits")
        self._root = Path(root)
        self._file_mode = file_mode
        self._validate_root()

    # ---- root validation -----------------------------------------------------------------------------------------

    def _validate_root(self) -> None:
        if self._root.is_symlink():
            raise UnsafePathError("store root must not be a symlink")
        if not self._root.is_absolute():
            raise UnsafePathError("store root must be an absolute path")
        self._root.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = _open_dir(str(self._root))
        try:
            st = os.fstat(fd)
            if st.st_uid != os.geteuid():
                raise UnsafePathError("store root must be owned by the current user")
            if st.st_mode & stat.S_IWOTH:
                raise UnsafePathError("store root must not be world-writable")
        finally:
            os.close(fd)

    @property
    def root(self) -> Path:
        return self._root

    # ---- directory fan-out (created one component at a time, never following symlinks) ------------------------------

    def _fanout(self, content_hash: ContentHash, *, create: bool) -> int | None:
        fd = _open_dir(str(self._root))
        try:
            for part in (content_hash.value[:2], content_hash.value[2:4]):
                try:
                    nxt = _open_dir(part, dir_fd=fd)
                except FileNotFoundError:
                    if not create:
                        return None
                    with contextlib.suppress(FileExistsError):
                        os.mkdir(part, 0o700, dir_fd=fd)
                    nxt = _open_dir(part, dir_fd=fd)
                os.close(fd)
                fd = nxt
            return fd
        except BaseException:
            os.close(fd)
            raise

    # ---- API -----------------------------------------------------------------------------------------------------

    def exists(self, content_hash: ContentHash) -> bool:
        dfd = self._fanout(content_hash, create=False)
        if dfd is None:
            return False
        try:
            st = os.stat(content_hash.value, dir_fd=dfd, follow_symlinks=False)
            return stat.S_ISREG(st.st_mode)
        except FileNotFoundError:
            return False
        finally:
            os.close(dfd)

    def open_read(self, content_hash: ContentHash) -> BinaryIO:
        dfd = self._fanout(content_hash, create=False)
        if dfd is None:
            raise FileNotFoundError(content_hash.value)
        try:
            fd = os.open(content_hash.value, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=dfd)
        finally:
            os.close(dfd)
        return os.fdopen(fd, "rb")

    def put(self, content_hash: ContentHash, source: BinaryIO, size: int) -> PutResult:
        dfd = self._fanout(content_hash, create=True)
        assert dfd is not None  # create=True
        tmp = f".tmp-{os.urandom(8).hex()}"
        tmp_created = False
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=dfd)
            tmp_created = True
            with os.fdopen(fd, "wb") as out:
                digest = hashlib.sha256()
                written = 0
                while chunk := source.read(_CHUNK):
                    digest.update(chunk)
                    written += len(chunk)
                    out.write(chunk)
                out.flush()
                os.fsync(out.fileno())
            if written != size or digest.hexdigest() != content_hash.value:
                raise VerifyAfterWriteError("received bytes do not match the expected size/hash")
            rfd = os.open(tmp, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=dfd)  # verify what is actually on disk
            try:
                on_disk, on_disk_size = _hash_fd(rfd)
            finally:
                os.close(rfd)
            if on_disk != content_hash.value or on_disk_size != size:
                raise VerifyAfterWriteError("bytes read back from disk do not match the expected hash")
            os.chmod(tmp, self._file_mode, dir_fd=dfd, follow_symlinks=False)
            try:
                os.link(tmp, content_hash.value, src_dir_fd=dfd, dst_dir_fd=dfd, follow_symlinks=False)  # fails if it exists
                created = True
            except FileExistsError:
                self._require_identical(dfd, content_hash)
                created = False
            os.fsync(dfd)
            return PutResult(content_hash=content_hash, relative_path=content_hash.relative_path, created=created)
        finally:
            if tmp_created:
                with contextlib.suppress(FileNotFoundError):
                    os.unlink(tmp, dir_fd=dfd)
            os.close(dfd)

    @staticmethod
    def _require_identical(dfd: int, content_hash: ContentHash) -> None:
        try:
            fd = os.open(content_hash.value, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=dfd)
        except OSError as exc:
            raise BlobCollisionError("existing path is not a regular file") from exc
        try:
            actual, _ = _hash_fd(fd)
        finally:
            os.close(fd)
        if actual != content_hash.value:
            raise BlobCollisionError("an existing blob has different bytes than its hash promises (corruption, P1)")


def ensure_disjoint_roots(a: Path, b: Path) -> None:
    """The published store and the quarantine store must never be the same directory or nested in one another."""
    ra, rb = Path(os.path.realpath(a)), Path(os.path.realpath(b))
    if ra == rb or ra in rb.parents or rb in ra.parents:
        raise UnsafePathError("blob store and quarantine store roots must be disjoint")
