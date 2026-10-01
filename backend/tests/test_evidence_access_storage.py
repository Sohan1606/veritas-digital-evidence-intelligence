"""Storage security for V2.2 reads: opening PRESERVED objects must be confined and non-blocking."""

from __future__ import annotations

import os
import threading
from pathlib import Path

import pytest

from app.services.evidence_signatures import SUPPORTED_MEDIA_TYPES, detected_media_type
from app.services.evidence_storage import EvidenceStorageFailure, LocalEvidenceStorage

KEY = "a" * 32
BODY = b"synthetic preserved bytes\n"


def _preserved(tmp_path: Path, key: str = KEY, body: bytes = BODY) -> LocalEvidenceStorage:
    """A store holding one genuinely preserved object, created through the real V2.1 calls."""
    store = LocalEvidenceStorage(tmp_path / "private")
    handle = store.create_quarantine_object(key)
    store.write_quarantine_chunk(handle, body)
    store.close_quarantine_object(handle)
    store.finalize_preserved_object(key)
    return store


def _preserved_path(tmp_path: Path, key: str = KEY) -> Path:
    return tmp_path / "private" / "preserved" / f"{key}.bin"


def _symlink_or_skip(link: Path, target: Path, *, directory: bool = False) -> None:
    try:
        link.symlink_to(target, target_is_directory=directory)
    except OSError as exc:
        if os.name == "nt" and getattr(exc, "winerror", None) == 1314:
            pytest.skip("Windows symlink privilege is unavailable (WinError 1314)")
        raise


def _open_fd_count() -> int | None:
    for directory in ("/proc/self/fd", "/dev/fd"):
        if os.path.isdir(directory):
            return len(os.listdir(directory))
    return None


def test_a_preserved_object_opens_read_only_with_the_exact_bytes(tmp_path: Path) -> None:
    store = _preserved(tmp_path)
    with store.open_preserved_object(KEY) as stream:
        assert stream.read(4) == BODY[:4]
        assert stream.read() == BODY[4:]
        with pytest.raises(OSError):  # the handle is read-only
            stream.write(b"x")


@pytest.mark.parametrize(
    "key",
    [
        "../outside",
        "nested/../../outside",
        r"..\outside",
        r"C:\outside",
        "nested/child",
        "a/b",
        r"a\b",
        f"{'a' * 32}/child",
        f"{'a' * 32}/../{'b' * 32}",
        f"{'a' * 32}\x00",
        "",
        "A" * 32,
        "a" * 31,
        "a" * 33,
        "g" * 32,
        " " + "a" * 31,
    ],
)
def test_path_like_and_malformed_keys_never_touch_the_filesystem(key: str, tmp_path: Path) -> None:
    store = LocalEvidenceStorage(tmp_path / "private")
    with pytest.raises(EvidenceStorageFailure):
        store.open_preserved_object(key)
    assert not (tmp_path / "private").exists()  # rejected before any directory is created


def test_an_absolute_path_is_not_a_key(tmp_path: Path) -> None:
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"must never be readable through the store")
    store = _preserved(tmp_path)
    with pytest.raises(EvidenceStorageFailure):
        store.open_preserved_object(str(outside))
    assert outside.read_bytes() == b"must never be readable through the store"


def test_a_missing_object_fails_without_leaking_a_path(tmp_path: Path) -> None:
    store = _preserved(tmp_path)
    with pytest.raises(EvidenceStorageFailure) as failure:
        store.open_preserved_object("b" * 32)
    assert str(failure.value) == "Evidence storage operation failed"
    assert str(tmp_path) not in str(failure.value) and failure.value.__cause__ is None


def test_a_quarantine_object_is_not_reachable_as_a_preserved_object(tmp_path: Path) -> None:
    store = LocalEvidenceStorage(tmp_path / "private")
    handle = store.create_quarantine_object(KEY)
    store.write_quarantine_chunk(handle, BODY)
    store.close_quarantine_object(handle)
    with pytest.raises(EvidenceStorageFailure):
        store.open_preserved_object(KEY)  # present only in quarantine: no fallback


def test_a_non_directory_root_is_rejected_and_left_untouched(tmp_path: Path) -> None:
    root_file = tmp_path / "private-root"
    root_file.write_bytes(b"do not replace the configured root")
    with pytest.raises(EvidenceStorageFailure):
        LocalEvidenceStorage(root_file).open_preserved_object(KEY)
    assert root_file.read_bytes() == b"do not replace the configured root"


def test_a_symlinked_configured_root_is_rejected(tmp_path: Path) -> None:
    store = _preserved(tmp_path)
    linked = tmp_path / "linked-private"
    _symlink_or_skip(linked, tmp_path / "private", directory=True)
    with pytest.raises(EvidenceStorageFailure):
        LocalEvidenceStorage(linked).open_preserved_object(KEY)
    with store.open_preserved_object(KEY) as stream:  # the real root is still fine
        assert stream.read() == BODY


def test_a_symlinked_preserved_directory_is_rejected(tmp_path: Path) -> None:
    store = _preserved(tmp_path)
    preserved = tmp_path / "private" / "preserved"
    moved = tmp_path / "elsewhere"
    preserved.rename(moved)
    _symlink_or_skip(preserved, moved, directory=True)
    with pytest.raises(EvidenceStorageFailure):
        store.open_preserved_object(KEY)


@pytest.mark.skipif(
    not getattr(os, "O_NOFOLLOW", 0),
    reason="this platform has no O_NOFOLLOW; links are not refused",
)
def test_a_symlinked_object_file_is_not_followed_even_with_identical_bytes(tmp_path: Path) -> None:
    store = _preserved(tmp_path)
    outside = tmp_path / "outside-copy.bin"
    outside.write_bytes(BODY)
    path = _preserved_path(tmp_path)
    path.unlink()
    _symlink_or_skip(path, outside)
    with pytest.raises(EvidenceStorageFailure):
        store.open_preserved_object(KEY)


def test_a_directory_in_place_of_the_object_is_rejected(tmp_path: Path) -> None:
    store = _preserved(tmp_path)
    path = _preserved_path(tmp_path)
    path.unlink()
    path.mkdir()
    with pytest.raises(EvidenceStorageFailure):
        store.open_preserved_object(KEY)


def test_a_named_pipe_in_place_of_the_object_is_rejected_without_blocking(tmp_path: Path) -> None:
    """A FIFO has no writer; a plain O_RDONLY open would block the worker thread forever."""
    mkfifo = getattr(os, "mkfifo", None)
    if not callable(mkfifo):
        pytest.skip("named pipes are unavailable here")

    store = _preserved(tmp_path)
    path = _preserved_path(tmp_path)
    path.unlink()
    mkfifo(path)
    outcome: list[BaseException | None] = []

    def attempt() -> None:
        try:
            store.open_preserved_object(KEY)
        except BaseException as exc:
            outcome.append(exc)
        else:
            outcome.append(None)

    worker = threading.Thread(target=attempt, daemon=True)
    worker.start()
    worker.join(timeout=5)
    if worker.is_alive():
        # Unblock the stuck opener so the test process can exit cleanly, then fail.
        nonblock = getattr(os, "O_NONBLOCK", 0)
        if not isinstance(nonblock, int):
            pytest.fail("O_NONBLOCK is unavailable on this platform")
        fd = os.open(path, os.O_WRONLY | nonblock) if path.exists() else -1
        if fd >= 0:
            os.close(fd)
        pytest.fail("opening a FIFO blocked the calling thread")
    assert isinstance(outcome[0], EvidenceStorageFailure)


def test_unreadable_permissions_are_a_sanitized_failure(tmp_path: Path) -> None:
    if not hasattr(os, "geteuid") or os.geteuid() == 0:
        pytest.skip("file permissions are not enforced for root or on this platform")
    store = _preserved(tmp_path)
    path = _preserved_path(tmp_path)
    path.chmod(0)
    try:
        with pytest.raises(EvidenceStorageFailure) as failure:
            store.open_preserved_object(KEY)
        assert str(tmp_path) not in str(failure.value)
    finally:
        path.chmod(0o600)


def test_a_failing_fstat_does_not_leak_the_file_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = _open_fd_count()
    if before is None:
        pytest.skip("open descriptors cannot be counted on this platform")
    store = _preserved(tmp_path)
    before = _open_fd_count()

    def broken_fstat(_fd: int) -> os.stat_result:
        raise OSError("synthetic fstat failure")

    monkeypatch.setattr(os, "fstat", broken_fstat)
    for _ in range(200):
        with pytest.raises(EvidenceStorageFailure):
            store.open_preserved_object(KEY)
    monkeypatch.undo()
    assert _open_fd_count() == before


def test_a_failing_fdopen_does_not_leak_the_file_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if _open_fd_count() is None:
        pytest.skip("open descriptors cannot be counted on this platform")
    store = _preserved(tmp_path)
    before = _open_fd_count()

    def broken_fdopen(*_args: object, **_kwargs: object) -> None:
        raise MemoryError("synthetic allocation failure")

    monkeypatch.setattr(os, "fdopen", broken_fdopen)
    for _ in range(200):
        with pytest.raises(MemoryError):
            store.open_preserved_object(KEY)
    monkeypatch.undo()
    assert _open_fd_count() == before


def test_repeated_open_and_close_leaves_no_descriptors(tmp_path: Path) -> None:
    if _open_fd_count() is None:
        pytest.skip("open descriptors cannot be counted on this platform")
    store = _preserved(tmp_path)
    before = _open_fd_count()
    for _ in range(300):
        with store.open_preserved_object(KEY) as stream:
            stream.read(1)
    assert _open_fd_count() == before


def test_every_detectable_media_type_is_in_the_served_allow_list() -> None:
    samples = [
        b"%PDF-1.7\n",
        b"\xff\xd8\xff\xe0x",
        b"\x89PNG\r\n\x1a\n",
        b"GIF89ax",
        b"RIFF\x10\x00\x00\x00WAVE",
        b"ID3x",
        b"\x00\x00\x00\x18ftypisom",
        b"PK\x03\x04x",
        b"plain text\n",
    ]
    detected = {detected_media_type(sample) for sample in samples}
    assert None not in detected
    assert detected == set(SUPPORTED_MEDIA_TYPES)
    assert "text/html" not in SUPPORTED_MEDIA_TYPES and "image/svg+xml" not in SUPPORTED_MEDIA_TYPES
