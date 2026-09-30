"""Deterministic, non-semantic leading-byte checks for supported basic formats."""

from __future__ import annotations

import codecs
import unicodedata
from dataclasses import dataclass

SIGNATURE_PREFIX_BYTES = 4096


@dataclass(frozen=True, slots=True)
class SignatureResult:
    detected_media_type: str | None
    accepted: bool
    note: str


# ZIP-based office/document types are reported as application/zip. This validator does
# not inspect archives or claim to distinguish their internal document formats.
_COMPATIBLE_DECLARATIONS: dict[str, frozenset[str]] = {
    "application/pdf": frozenset({"application/pdf"}),
    "image/jpeg": frozenset({"image/jpeg"}),
    "image/png": frozenset({"image/png"}),
    "image/gif": frozenset({"image/gif"}),
    "audio/wav": frozenset({"audio/wav", "audio/x-wav"}),
    "audio/mpeg": frozenset({"audio/mpeg", "audio/mp3"}),
    "video/mp4": frozenset({"video/mp4"}),
    "application/zip": frozenset(
        {
            "application/zip",
            "application/x-zip-compressed",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "application/vnd.oasis.opendocument.text",
            "application/vnd.oasis.opendocument.spreadsheet",
            "application/vnd.oasis.opendocument.presentation",
        }
    ),
    "text/plain": frozenset({"text/plain"}),
}


def _is_utf8_text(prefix: bytes) -> bool:
    decoder = codecs.getincrementaldecoder("utf-8")("strict")
    try:
        text = decoder.decode(prefix, final=False)
    except UnicodeDecodeError:
        return False
    return all(
        character in "\t\n\r" or unicodedata.category(character) not in {"Cc", "Cs"}
        for character in text
    )


def detected_media_type(prefix: bytes) -> str | None:
    """Identify a supported coarse format from only a bounded leading-byte prefix."""
    if prefix.startswith(b"%PDF-"):
        return "application/pdf"
    if prefix.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if prefix.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if prefix.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(prefix) >= 12 and prefix[:4] == b"RIFF" and prefix[8:12] == b"WAVE":
        return "audio/wav"
    if prefix.startswith(b"ID3") or (
        len(prefix) >= 2 and prefix[0] == 0xFF and prefix[1] & 0xE0 == 0xE0
    ):
        return "audio/mpeg"
    if len(prefix) >= 12 and prefix[4:8] == b"ftyp":
        return "video/mp4"
    if prefix.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")):
        return "application/zip"
    if len(prefix) <= SIGNATURE_PREFIX_BYTES and _is_utf8_text(prefix):
        return "text/plain"
    return None


def validate_signature(prefix: bytes, declared_media_type: str) -> SignatureResult:
    """Return a controlled result; never parse containers or trust extensions."""
    if len(prefix) > SIGNATURE_PREFIX_BYTES:
        raise ValueError("signature prefix exceeds the bounded inspection size")
    detected = detected_media_type(prefix)
    if detected is None:
        return SignatureResult(
            detected_media_type=None,
            accepted=False,
            note="The uploaded bytes do not match a supported basic file signature.",
        )
    if declared_media_type not in _COMPATIBLE_DECLARATIONS[detected]:
        return SignatureResult(
            detected_media_type=detected,
            accepted=False,
            note="The detected signature is incompatible with the declared media type.",
        )
    return SignatureResult(
        detected_media_type=detected,
        accepted=True,
        note="A supported leading-byte signature matched the declared media type.",
    )
