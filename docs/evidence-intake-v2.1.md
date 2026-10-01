# V2.1 Evidence Intake & Integrity Foundation — implementation notes

This document describes the V2.1 implementation on `v2.1-evidence-intake` and its operational boundaries. It supplements—but does not replace—the source document at [`v2.1-evidence-intake-spec.md`](v2.1-evidence-intake-spec.md). These notes describe implementation behavior and verification boundaries.

V2.1 adds authenticated, Case-authorized intake mechanics to the cumulative V2 identity/access foundation. It does not provide forensic examination, semantic file parsing, authenticity findings, AI analysis, public upload, or generic evidence download.

## Canonical records and state

- Existing `Evidence` remains the logical Case evidence record; existing `EvidenceProfile` remains its single profile projection. Intake data does not create a second logical Evidence or integrity-profile concept.
- Each captured byte sequence is represented by an immutable `EvidenceObject` associated with its existing Evidence. A later acquisition is a new object, not an edit to a prior object.
- EvidenceObject transitions are limited to `QUARANTINED → PRESERVED` or `QUARANTINED → REJECTED`. Terminal objects cannot transition again. Preserved object metadata and bytes are treated as immutable.
- The append-only `EvidenceCustodyEvent` projection records only `RECEIVED` and `PRESERVED` in V2.1. It is not a transfer or hand-off workflow. The canonical `AuditEvent` remains the system audit stream; custody events do not replace it.
- SHA-256, SHA-512, detected media type, and size are derived from the streamed bytes. These values describe the bytes VERITAS received; they do not establish authenticity, acquisition provenance, forensic certification, or legal admissibility.

## HTTP API

All routes are same-origin, require server-side authentication, active Organization membership, Case authorization, and the capability shown below. Actors, Organization, Case association, IDs, timestamps, hashes, and storage keys are resolved or generated server-side.

| Method and path | Capability | Purpose |
| --- | --- | --- |
| `POST /api/v1/cases/{case_id}/evidence/intake` | `evidence:intake` | Create the canonical Evidence record and its initial quarantined object metadata; no bytes are sent yet. |
| `POST /api/v1/cases/{case_id}/evidence/{evidence_id}/objects` | `evidence:intake` | Register a new acquisition object for an existing Evidence record. |
| `PUT /api/v1/cases/{case_id}/evidence/{evidence_id}/objects/{object_id}/content` | `evidence:intake` | Stream raw `application/octet-stream` bytes into private quarantine and compute server-side digests. |
| `POST /api/v1/cases/{case_id}/evidence/{evidence_id}/objects/{object_id}/finalize` | `evidence:intake` **and** `custody:write` | Validate the upload and atomically preserve accepted bytes, or reject the object. |
| `GET /api/v1/cases/{case_id}/evidence/{evidence_id}/intake` | `evidence:read` | Read Evidence and its EvidenceObject intake/integrity metadata. |
| `GET /api/v1/cases/{case_id}/evidence/{evidence_id}/objects/{object_id}/custody` | `custody:read` | Read append-only custody events for that Case-scoped object. |

The Investigator role can register and stream but does not hold `custody:write`, so it cannot finalize. The Custodian role has the intake and custody-write capabilities needed for finalization. Reviewer and Researcher do not receive intake or custody-write authority. Administrator identity privileges do not imply evidence access. Frontend gating is only a usability measure; the API dependencies enforce the policy on direct calls.

There is deliberately **no generic evidence-byte download route**. The UI displays metadata, digests, validation result, lifecycle and authorized custody history, not the stored evidence bytes or filesystem location.

> **Superseded in part by V2.2.** V2.2 adds authenticated, Case-authorized retrieval of a PRESERVED EvidenceObject's bytes and an independent integrity verification (`GET …/content`, `POST …/verify`, both requiring `evidence:read`). There is still no *generic* or path-based download, and the filesystem location is never exposed. See [evidence-retrieval-verification-v2.2.md](evidence-retrieval-verification-v2.2.md).

### Demonstration-mode boundary

No V2.1 intake or additional EvidenceObject creation is permitted for a Case marked `is_demonstration`, even for an authenticated user with a Case assignment; both centralized authorization and the intake service enforce this. The anonymous V1 demonstration viewer may continue to list synthetic Evidence and use its V1 Evidence Profile route, but cannot read V2.1 intake metadata or custody events. V2.1 object-detail and custody endpoints reject demonstration Cases even for authenticated callers, and the existing profile endpoint omits EvidenceObject-derived hashes and other integrity values for every demonstration Case. Authenticated restricted-mode users retain the V2.1 workflow on non-demonstration Cases. These controls are enforced server-side, not just by UI visibility.

## Streaming, validation and storage

- The browser sends raw bytes to the same-origin API; the API consumes the request stream in bounded chunks rather than materializing the complete file in memory.
- `VERITAS_MAX_EVIDENCE_BYTES` defaults to `104857600` bytes (100 MiB; configurable from 1 byte through 1 GiB). The intake service counts actual streamed bytes and enforces the limit independently of `Content-Length`. The default nginx `client_max_body_size` is also 100 MiB; deployments raising the application limit must separately review the proxy ceiling.
- SHA-256 and SHA-512 are computed while writing quarantine. A bounded leading-byte signature check supports a small, deterministic set of coarse types (PDF, JPEG, PNG, GIF, WAV, MPEG audio, MP4, ZIP-family containers as ZIP, and UTF-8 text). It does not parse containers, render content, scan malware, or interpret evidence. Unsupported signatures and incompatible declarations are rejected, not classified as forensic findings.
- `LocalEvidenceStorage` is the storage abstraction implementation. It creates opaque server-generated keys exclusively, writes under separate private quarantine/preserved directories, and publishes accepted content without replacing an existing preserved object. File contents are fsynced before publication. POSIX directory-entry fsync is retained; Windows explicitly reports directory fsync as unsupported and completes publication after the file-content fsync, because portable directory fsync is unavailable there. Consequently, Windows does not claim directory-entry durability across power loss. Other storage I/O errors remain failures, and client-visible storage errors are sanitized.
- The default local root is `./data/evidence`; Compose configures `/var/lib/veritas/evidence` on a named `evidencedata` volume mounted only into the backend. The web/nginx service does not mount this volume. Evidence bytes are outside PostgreSQL and are not exposed as static files.
- Local filesystem permissions and application state transitions are safeguards, not WORM storage or cryptographic tamper evidence. Backups, encryption at rest, host access, filesystem integrity, restore procedures and operational monitoring remain deployment responsibilities.

## Database and audit

Alembic revision `0003` (`0003_evidence_intake_integrity`) adds the EvidenceObject and EvidenceCustodyEvent tables, constraints and public-ID allocation updates, and installs PostgreSQL append-only protection for custody events. ORM guards and SQLite migration/integration tests cover the application-side rules. The PostgreSQL-only trigger test is skipped when PostgreSQL is unavailable; a SQLite pass does **not** prove PostgreSQL trigger or production migration behavior.

Canonical `AuditEvent` records continue to describe relevant actions. No duplicate general audit store is introduced. Custody read authorization remains Case-scoped and separate from general evidence-read capability.

## Verification boundary

Synthetic backend regression fixtures use deterministic byte strings only; they are not evidence samples. The automated suite covers workflow transitions, streamed limits/digests, signature outcomes, authorization and Organization isolation, storage cleanup/failure, ORM immutability, and SQLite migration upgrade/downgrade/re-upgrade. The PostgreSQL-only trigger test requires an available PostgreSQL test database. Docker Compose runtime, private-volume persistence and container-level nginx/backend isolation require Docker; they were not verified in the current environment when Docker was unavailable.

The canonical V2.1 specification is complete through Section 51, “Test requirements — audit,” and defines the V2.1 scope and test requirements. Section 51 explicitly states that it completes the V2.1 test requirements.
