"""Private local evidence storage with opaque keys and no overwrite semantics."""

from __future__ import annotations

import os
import re
import stat
from contextlib import suppress
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import BinaryIO, Protocol

_STORAGE_KEY = re.compile(r"^[0-9a-f]{32}$")
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_NONBLOCK = getattr(os, "O_NONBLOCK", 0)


class DirectoryFsyncResult(Enum):
    """Result of syncing directory entries after a storage operation."""

    SYNCED = "synced"
    UNSUPPORTED = "unsupported"


class EvidenceStorageFailure(RuntimeError):
    """Sanitized internal storage failure; deliberately carries no path or OS detail."""


@dataclass(slots=True)
class QuarantineHandle:
    _stream: BinaryIO = field(repr=False)
    _closed: bool = False


class EvidenceStorage(Protocol):
    def create_quarantine_object(self, storage_key: str) -> QuarantineHandle: ...

    def write_quarantine_chunk(self, handle: QuarantineHandle, chunk: bytes) -> None: ...

    def close_quarantine_object(self, handle: QuarantineHandle) -> None: ...

    def open_quarantine_object(self, storage_key: str) -> BinaryIO: ...

    def open_preserved_object(self, storage_key: str) -> BinaryIO: ...

    def finalize_preserved_object(self, storage_key: str) -> None: ...

    def discard_quarantine_object(self, storage_key: str) -> None: ...

    def exists(self, storage_key: str, *, preserved: bool = False) -> bool: ...


class LocalEvidenceStorage:
    """A single-filesystem store; quarantine and preserved directories stay private."""

    def __init__(self, root: Path) -> None:
        self._configured_root = root.expanduser()

    @staticmethod
    def _validate_key(storage_key: str) -> None:
        if not _STORAGE_KEY.fullmatch(storage_key):
            raise EvidenceStorageFailure("Evidence storage operation failed")

    @staticmethod
    def _reject_linked_components(path: Path) -> None:
        """Reject symlink/junction path components before using a storage directory."""
        absolute = Path(os.path.abspath(path))
        components = reversed((absolute, *absolute.parents))
        for component in components:
            is_junction = getattr(component, "is_junction", None)
            if component.is_symlink() or (callable(is_junction) and is_junction()):
                raise OSError("storage path contains a link")

    def _directory(self, name: str) -> Path:
        try:
            self._reject_linked_components(self._configured_root)
            self._configured_root.mkdir(mode=0o700, parents=True, exist_ok=True)
            self._reject_linked_components(self._configured_root)
            root = self._configured_root.resolve(strict=True)
            os.chmod(root, 0o700)
            directory = root / name
            directory.mkdir(mode=0o700, exist_ok=True)
            self._reject_linked_components(directory)
            if not directory.is_dir():
                raise OSError("invalid evidence storage directory")
            os.chmod(directory, 0o700)
            return directory
        except OSError:
            raise EvidenceStorageFailure("Evidence storage operation failed") from None

    @staticmethod
    def _file_path(directory: Path, storage_key: str, suffix: str) -> Path:
        return directory / f"{storage_key}{suffix}"

    @staticmethod
    def _fsync_directory(directory: Path) -> DirectoryFsyncResult:
        """Sync a directory entry where supported, or report the Windows limitation.

        File contents are fsynced before publication. Windows does not expose a portable
        directory-fsync primitive through this API, so Windows cannot claim the same
        directory-entry durability guarantee as POSIX. Callers may continue publication
        when this helper explicitly reports ``UNSUPPORTED``. All actual POSIX I/O errors
        still propagate to the storage operation and are sanitized there.
        """
        if os.name == "nt":
            return DirectoryFsyncResult.UNSUPPORTED
        descriptor = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return DirectoryFsyncResult.SYNCED

    @staticmethod
    def _open_existing(path: Path) -> BinaryIO:
        """Open an existing regular file read-only, never following a link or blocking.

        ``O_NONBLOCK`` stops a FIFO swapped in for a stored object from blocking the calling
        thread inside ``open``; the regular-file check then rejects it. The descriptor is
        closed on every failure path, so a failed open cannot leak a file descriptor.
        """
        descriptor = -1
        try:
            descriptor = os.open(path, os.O_RDONLY | _NOFOLLOW | _NONBLOCK)
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise OSError("invalid evidence object")
            stream = os.fdopen(descriptor, "rb")
            descriptor = -1  # the file object now owns the descriptor
            return stream
        except OSError:
            raise EvidenceStorageFailure("Evidence storage operation failed") from None
        finally:
            if descriptor >= 0:
                with suppress(OSError):
                    os.close(descriptor)

    def create_quarantine_object(self, storage_key: str) -> QuarantineHandle:
        self._validate_key(storage_key)
        directory = self._directory("quarantine")
        path = self._file_path(directory, storage_key, ".part")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW
        try:
            descriptor = os.open(path, flags, 0o600)
            return QuarantineHandle(os.fdopen(descriptor, "wb"))
        except OSError:
            raise EvidenceStorageFailure("Evidence storage operation failed") from None

    def write_quarantine_chunk(self, handle: QuarantineHandle, chunk: bytes) -> None:
        if handle._closed:
            raise EvidenceStorageFailure("Evidence storage operation failed")
        try:
            handle._stream.write(chunk)
        except OSError:
            raise EvidenceStorageFailure("Evidence storage operation failed") from None

    def close_quarantine_object(self, handle: QuarantineHandle) -> None:
        if handle._closed:
            return
        handle._closed = True
        try:
            handle._stream.flush()
            os.fsync(handle._stream.fileno())
            handle._stream.close()
        except OSError:
            with suppress(OSError):
                handle._stream.close()
            raise EvidenceStorageFailure("Evidence storage operation failed") from None

    def open_quarantine_object(self, storage_key: str) -> BinaryIO:
        self._validate_key(storage_key)
        return self._open_existing(
            self._file_path(self._directory("quarantine"), storage_key, ".part")
        )

    def open_preserved_object(self, storage_key: str) -> BinaryIO:
        self._validate_key(storage_key)
        return self._open_existing(
            self._file_path(self._directory("preserved"), storage_key, ".bin")
        )

    def exists(self, storage_key: str, *, preserved: bool = False) -> bool:
        self._validate_key(storage_key)
        directory_name, suffix = ("preserved", ".bin") if preserved else ("quarantine", ".part")
        path = self._file_path(self._directory(directory_name), storage_key, suffix)
        try:
            info = path.lstat()
        except FileNotFoundError:
            return False
        except OSError:
            raise EvidenceStorageFailure("Evidence storage operation failed") from None
        if not stat.S_ISREG(info.st_mode):
            raise EvidenceStorageFailure("Evidence storage operation failed")
        return True

    def finalize_preserved_object(self, storage_key: str) -> None:
        """Atomically publish the quarantined inode without replacing an existing object."""
        self._validate_key(storage_key)
        quarantine = self._directory("quarantine")
        preserved = self._directory("preserved")
        source = self._file_path(quarantine, storage_key, ".part")
        destination = self._file_path(preserved, storage_key, ".bin")
        try:
            try:
                source_info = source.lstat()
            except FileNotFoundError:
                # Idempotent recovery after a storage move succeeded but DB commit did not.
                destination_info = destination.lstat()
                if stat.S_ISREG(destination_info.st_mode):
                    return
                raise OSError("invalid evidence object") from None
            if not stat.S_ISREG(source_info.st_mode):
                raise OSError("invalid evidence object")
            try:
                os.link(source, destination, follow_symlinks=False)
            except FileExistsError:
                if not os.path.samefile(source, destination):
                    raise OSError("evidence destination already exists") from None

            # Make the preserved directory entry durable before removing the quarantine
            # name. On Windows, the helper reports the directory-sync limitation explicitly;
            # the content itself was fsynced when the quarantine stream was closed.
            preserved_sync = self._fsync_directory(preserved)
            source.unlink()
            quarantine_sync = self._fsync_directory(quarantine)
            if (
                preserved_sync is DirectoryFsyncResult.UNSUPPORTED
                or quarantine_sync is DirectoryFsyncResult.UNSUPPORTED
            ):
                # Publication succeeded, and no supported directory-fsync primitive exists.
                return
        except OSError:
            raise EvidenceStorageFailure("Evidence storage operation failed") from None

    def discard_quarantine_object(self, storage_key: str) -> None:
        self._validate_key(storage_key)
        directory = self._directory("quarantine")
        path = self._file_path(directory, storage_key, ".part")
        try:
            path.unlink(missing_ok=True)
            directory_sync = self._fsync_directory(directory)
            if directory_sync is DirectoryFsyncResult.UNSUPPORTED:
                # The deletion completed; Windows has no supported directory-fsync primitive.
                return
        except OSError:
            raise EvidenceStorageFailure("Evidence storage operation failed") from None
