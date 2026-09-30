"""Unit tests for bounded digests, coarse signatures and private storage behavior."""

from __future__ import annotations

import hashlib
import os
from io import BytesIO
from pathlib import Path

import pytest

from app.services.evidence_hashing import HASH_CHUNK_BYTES, StreamingDigest, digest_file
from app.services.evidence_signatures import validate_signature
from app.services.evidence_storage import (
    DirectoryFsyncResult,
    EvidenceStorageFailure,
    LocalEvidenceStorage,
)


@pytest.mark.parametrize(
    ("prefix", "declared", "detected"),
    [
        (b"%PDF-1.7\n", "application/pdf", "application/pdf"),
        (b"\xff\xd8\xff\xe0synthetic", "image/jpeg", "image/jpeg"),
        (b"\x89PNG\r\n\x1a\nsynthetic", "image/png", "image/png"),
        (b"GIF89asynthetic", "image/gif", "image/gif"),
        (b"RIFF\x10\x00\x00\x00WAVE", "audio/wav", "audio/wav"),
        (b"ID3synthetic", "audio/mpeg", "audio/mpeg"),
        (b"\x00\x00\x00\x18ftypisom", "video/mp4", "video/mp4"),
        (
            b"PK\x03\x04synthetic",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/zip",
        ),
        (b"plain UTF-8 test\n", "text/plain", "text/plain"),
        (b"", "text/plain", "text/plain"),
    ],
)
def test_supported_basic_signature_results(prefix: bytes, declared: str, detected: str) -> None:
    result = validate_signature(prefix, declared)
    assert result.accepted is True
    assert result.detected_media_type == detected


def test_signature_mismatch_and_unsupported_binary_are_rejected() -> None:
    mismatch = validate_signature(b"\x89PNG\r\n\x1a\n", "application/pdf")
    assert mismatch.accepted is False
    assert mismatch.detected_media_type == "image/png"
    unsupported = validate_signature(b"\x00\xff\x00\xff", "application/octet-stream")
    assert unsupported.accepted is False
    assert unsupported.detected_media_type is None
    assert "signature" in unsupported.note


def test_zip_compatibility_is_coarse_and_does_not_claim_document_identity() -> None:
    result = validate_signature(
        b"PK\x03\x04",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    assert result.accepted
    assert result.detected_media_type == "application/zip"


def test_streaming_hashes_match_standard_library_without_retaining_content() -> None:
    body = b"%PDF-1.7\n" + bytes(range(256)) * 900
    accumulator = StreamingDigest()
    for offset in range(0, len(body), HASH_CHUNK_BYTES):
        accumulator.update(body[offset : offset + HASH_CHUNK_BYTES])
    summary = accumulator.finish()
    assert summary.byte_size == len(body)
    assert summary.sha256 == hashlib.sha256(body).hexdigest()
    assert summary.sha512 == hashlib.sha512(body).hexdigest()
    empty = StreamingDigest().finish()
    assert empty.byte_size == 0
    assert empty.sha256 == hashlib.sha256(b"").hexdigest()
    assert empty.sha512 == hashlib.sha512(b"").hexdigest()


def test_digest_file_uses_bounded_reads() -> None:
    body = b"synthetic" * 20_000
    stream = BytesIO(body)
    summary = digest_file(stream)
    assert summary.byte_size == len(body)
    assert summary.sha256 == hashlib.sha256(body).hexdigest()


def test_local_storage_creates_exclusively_and_preserves_atomically(tmp_path: Path) -> None:
    store = LocalEvidenceStorage(tmp_path / "private")
    key = "a" * 32
    handle = store.create_quarantine_object(key)
    store.write_quarantine_chunk(handle, b"synthetic bytes")
    store.close_quarantine_object(handle)
    with pytest.raises(EvidenceStorageFailure):
        store.create_quarantine_object(key)
    store.finalize_preserved_object(key)
    assert store.exists(key, preserved=True)
    assert not store.exists(key, preserved=False)
    with store.open_preserved_object(key) as stream:
        assert stream.read() == b"synthetic bytes"
    store.discard_quarantine_object(key)
    assert store.exists(key, preserved=True)


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
    ],
)
def test_storage_rejects_path_like_keys(key: str, tmp_path: Path) -> None:
    store = LocalEvidenceStorage(tmp_path / "private")
    with pytest.raises(EvidenceStorageFailure):
        store.create_quarantine_object(key)
    assert not (tmp_path / "private").exists()


def test_storage_rejects_absolute_paths_without_writing_outside(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    store = LocalEvidenceStorage(tmp_path / "private")
    with pytest.raises(EvidenceStorageFailure):
        store.create_quarantine_object(str(outside))
    assert not outside.exists()
    assert not (tmp_path / "private").exists()


def test_storage_rejects_a_non_directory_configured_root(tmp_path: Path) -> None:
    root_file = tmp_path / "private-root"
    root_file.write_bytes(b"do not replace the configured root")
    store = LocalEvidenceStorage(root_file)

    with pytest.raises(EvidenceStorageFailure):
        store.create_quarantine_object("a" * 32)
    assert root_file.read_bytes() == b"do not replace the configured root"


def _create_directory_symlink_or_skip(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError as exc:
        if os.name == "nt" and getattr(exc, "winerror", None) == 1314:
            pytest.skip("Windows symlink privilege is unavailable (WinError 1314)")
        raise


@pytest.mark.parametrize("directory_name", ["quarantine", "preserved"])
def test_storage_rejects_symlinked_private_directories(directory_name: str, tmp_path: Path) -> None:
    root = tmp_path / "private"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    store = LocalEvidenceStorage(root)
    key = "b" * 32

    if directory_name == "preserved":
        handle = store.create_quarantine_object(key)
        store.write_quarantine_chunk(handle, b"private source")
        store.close_quarantine_object(handle)

    _create_directory_symlink_or_skip(root / directory_name, outside)
    with pytest.raises(EvidenceStorageFailure):
        if directory_name == "quarantine":
            store.create_quarantine_object(key)
        else:
            store.finalize_preserved_object(key)
    assert list(outside.iterdir()) == []


def test_storage_rejects_symlinked_configured_root(tmp_path: Path) -> None:
    actual_root = tmp_path / "actual-private"
    actual_root.mkdir()
    linked_root = tmp_path / "linked-private"
    _create_directory_symlink_or_skip(linked_root, actual_root)

    store = LocalEvidenceStorage(linked_root)
    with pytest.raises(EvidenceStorageFailure):
        store.create_quarantine_object("c" * 32)
    assert list(actual_root.iterdir()) == []


def test_windows_directory_fsync_reports_unsupported(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    with monkeypatch.context() as platform:
        platform.setattr(os, "name", "nt")
        platform.setattr(
            os,
            "open",
            lambda *_args, **_kwargs: pytest.fail("Windows directory open must be skipped"),
        )
        platform.setattr(
            os,
            "fsync",
            lambda *_args, **_kwargs: pytest.fail("Windows directory fsync must be skipped"),
        )
        result = LocalEvidenceStorage._fsync_directory(tmp_path)
    assert result is DirectoryFsyncResult.UNSUPPORTED


def test_finalize_succeeds_when_directory_fsync_is_unsupported(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store = LocalEvidenceStorage(tmp_path / "private")
    key = "d" * 32
    handle = store.create_quarantine_object(key)
    store.write_quarantine_chunk(handle, b"durable file contents")
    store.close_quarantine_object(handle)
    synced_directories: list[Path] = []

    def unsupported_directory_sync(directory: Path) -> DirectoryFsyncResult:
        synced_directories.append(directory)
        return DirectoryFsyncResult.UNSUPPORTED

    monkeypatch.setattr(
        LocalEvidenceStorage,
        "_fsync_directory",
        staticmethod(unsupported_directory_sync),
    )
    store.finalize_preserved_object(key)

    assert len(synced_directories) == 2
    assert store.exists(key, preserved=True)
    assert not store.exists(key, preserved=False)
    with store.open_preserved_object(key) as stream:
        assert stream.read() == b"durable file contents"


def test_finalize_surfaces_directory_sync_error_without_removing_source(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store = LocalEvidenceStorage(tmp_path / "private")
    key = "e" * 32
    handle = store.create_quarantine_object(key)
    store.write_quarantine_chunk(handle, b"source remains until directory sync succeeds")
    store.close_quarantine_object(handle)

    def fail_directory_sync(_directory: Path) -> DirectoryFsyncResult:
        raise OSError("synthetic directory sync failure")

    monkeypatch.setattr(
        LocalEvidenceStorage,
        "_fsync_directory",
        staticmethod(fail_directory_sync),
    )
    with pytest.raises(EvidenceStorageFailure):
        store.finalize_preserved_object(key)

    # The hard link was created, but the quarantine name is kept because the
    # preserved-directory durability step failed before source cleanup.
    assert store.exists(key, preserved=False)
    assert store.exists(key, preserved=True)
    with store.open_quarantine_object(key) as stream:
        assert stream.read() == b"source remains until directory sync succeeds"


def test_finalize_surfaces_link_failure_and_keeps_quarantine_source(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    store = LocalEvidenceStorage(tmp_path / "private")
    key = "e" * 32
    handle = store.create_quarantine_object(key)
    store.write_quarantine_chunk(handle, b"source stays private")
    store.close_quarantine_object(handle)

    def fail_link(*args: object, **kwargs: object) -> None:
        raise OSError("synthetic link failure")

    monkeypatch.setattr(os, "link", fail_link)
    with pytest.raises(EvidenceStorageFailure):
        store.finalize_preserved_object(key)

    assert store.exists(key, preserved=False)
    assert not store.exists(key, preserved=True)
    with store.open_quarantine_object(key) as stream:
        assert stream.read() == b"source stays private"


def test_finalize_does_not_overwrite_an_existing_preserved_object(tmp_path: Path) -> None:
    store = LocalEvidenceStorage(tmp_path / "private")
    key = "f" * 32
    handle = store.create_quarantine_object(key)
    store.write_quarantine_chunk(handle, b"new content")
    store.close_quarantine_object(handle)
    existing = store._directory("preserved") / f"{key}.bin"
    existing.write_bytes(b"preexisting content")

    with pytest.raises(EvidenceStorageFailure):
        store.finalize_preserved_object(key)
    assert store.exists(key, preserved=False)
    with store.open_preserved_object(key) as stream:
        assert stream.read() == b"preexisting content"
