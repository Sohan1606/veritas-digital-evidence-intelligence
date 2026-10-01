# V2.2 Evidence Retrieval & Integrity Verification — implementation notes

> **INTEGRITY MATCH IS NOT AUTHENTICITY PROOF.**
> V2.2 can return the exact bytes VERITAS preserved and can independently recompute their byte
> count, SHA-256 and SHA-512 against the values recorded at intake. A `MATCH` says only that the
> stored bytes still equal what was recorded when they were preserved. It does not say the
> evidence is genuine, unaltered before intake, forensically reliable or legally admissible, and
> VERITAS never reports it as such.

This supplements the V2.1 notes ([evidence-intake-v2.1.md](evidence-intake-v2.1.md)). It adds two
operations on a **PRESERVED EvidenceObject** and nothing else: authorized retrieval and
independent integrity verification.

## Scope

- No migration, no new table, no new capability. Alembic head is unchanged (`0003`).
- Both operations use the existing `evidence:read` capability and the existing Case → Evidence →
  EvidenceObject resolution, `EvidenceStorage`, hashing primitives, `AuditEvent` and error
  envelope. There is no parallel download, verification, audit or storage system.
- Not added: examination of any kind, scoring, reports, Case Packages, external verifiers, WORM
  storage, signed manifests, public endpoints, arbitrary-path or URL retrieval.

## Endpoints

| Method and path | Purpose | Success | Errors |
| --- | --- | --- | --- |
| `GET /api/v1/cases/{case_id}/evidence/{evidence_id}/objects/{object_id}/content` | Stream the preserved bytes | `200` binary | `401`, `404`, `409`, `422`, `503` |
| `POST /api/v1/cases/{case_id}/evidence/{evidence_id}/objects/{object_id}/verify` | Recompute and compare | `200` JSON | `401`, `403` (CSRF), `404`, `409`, `422`, `500` |

`GET …/content` shares its path with the V2.1 `PUT` upload; the methods are independent. `HEAD`
is not routed, and `Range` requests are ignored (the whole object is returned).

All client errors use the existing `{"error": {"code", "message", "request_id"}}` envelope. No
response, log line or audit record contains bytes, a filesystem path, a storage key, a credential
or an internal UUID.

### Authorization

Decided before any storage access, in this order: authentication → `evidence:read` for the Case
(demonstration Cases and the anonymous demo viewer are refused) → Evidence within that Case →
EvidenceObject within that Case and Evidence → state `PRESERVED`.

| Caller | Result |
| --- | --- |
| Investigator, Reviewer, Custodian, Researcher (assigned to the Case) | allowed |
| Auditor, within its authorized scope (organization-wide or Case-scoped) | allowed |
| Administrator (no `evidence:read`) | `404` (masked) |
| Anonymous demo viewer; any caller on a demonstration Case | `404` |
| Unauthenticated, disabled user, expired or revoked session | `401` |
| Another Case, another Organization, guessed or mismatched identifiers | `404` |
| `QUARANTINED` or `REJECTED` object | `409 evidence_state_conflict` |
| Malformed identifiers (for an authorized caller) | `422` |

A denied caller cannot tell an existing object from a nonexistent one. Authorization failures are
never reported as `UNAVAILABLE`. `POST …/verify` requires the session-bound CSRF token like every
other state-changing request.

## Storage design

- Bytes are read **only** through `EvidenceStorage.open_preserved_object(storage_key)`; the key
  comes from the database row, never from the client. There is no quarantine fallback and no
  alternate location. A byte-identical copy in quarantine or elsewhere is never used.
- Every read is bounded by `HASH_CHUNK_BYTES` (64 KiB). Nothing reads a whole object, and
  nothing writes a temporary copy. Memory use does not grow with object size.
- `LocalEvidenceStorage` still refuses symlinked or non-directory roots and directories, rejects
  path-like keys before touching the filesystem, and opens files with `O_NOFOLLOW`. V2.2 also made
  `_open_existing` (a) close its descriptor on every failure path and (b) use `O_NONBLOCK`, so a
  FIFO placed where a preserved object should be is rejected instead of blocking a worker thread
  (`test_a_named_pipe_in_place_of_the_object_is_rejected_without_blocking` fails against the V2.1
  implementation).
- No database connection is held during storage I/O: the read transaction is ended before the
  preserved object is opened, so a slow download does not pin a pooled connection (tests count the
  pool's checked-out connections during every storage read, and during a deliberately slow
  download over a real socket).

## Retrieval semantics

- **Headers.** `Content-Type` is the server-detected `detected_media_type`, used only if it is one
  of the types the signature validator can produce (`SUPPORTED_MEDIA_TYPES`), else
  `application/octet-stream`; no charset is ever asserted. `Content-Disposition` is
  `attachment; filename="<EOBJ id>.bin"`, deterministic from the public identifier; the submitter's
  `original_filename` is never used. `X-Content-Type-Options: nosniff`, `Cache-Control: no-store`
  and the `default-src 'none'` CSP come from the existing global security-headers middleware
  (nosniff is deliberately not duplicated). `Content-Length` is the file's size when it was
  opened.
- **Early failure is a clean error.** The object is opened and its first chunk is read before any
  header is sent, so a missing, unreadable or unreadable-at-first-read object returns the existing
  `503 evidence_storage_unavailable` envelope rather than a broken `200`.
- **Retrieval is not verification.** It serves what is stored. After a `MISMATCH`, retrieval stays
  enabled and returns the current bytes with their actual length. A file that shrinks while a
  transfer is in progress aborts the response (the declared length is never silently short); one
  that grows is capped at the length advertised at open.
- **Audit timing.** One `evidence.object.retrieved` event is committed **before the final chunk is
  released**. So no complete copy leaves the server without a committed event, there is never an
  event per chunk, and a failed or interrupted transfer never records one. A single-chunk or empty
  object is audited before the response exists; if that write fails the caller gets an error and
  no bytes. The event records that the last chunk was handed to the transport; it cannot prove the
  client received it.
- **Failures after headers.** Once a `200` has started, a storage read error or an audit-write
  failure aborts the connection (the client sees an incomplete body against `Content-Length`). It
  is logged as `evidence_retrieval_failed` without path or key. A client disconnect closes the
  preserved-object handle deterministically (`RetrievalResponse`), writes no event and mutates
  nothing.
- **Reverse proxy.** The content route must not be response-buffered by a proxy. The shipped
  `docker/nginx.conf` sets `proxy_buffering off` and `proxy_max_temp_file_size 0` for it; without
  that, nginx would spool evidence responses to its own disk.

## Verification semantics

The service recomputes byte count, SHA-256 and SHA-512 with bounded reads and compares all three
with the immutable intake values. It takes no row lock and writes nothing but one `AuditEvent`
(and that event's identifier allocation, the mechanism every AuditEvent uses).

| Result | Meaning |
| --- | --- |
| `MATCH` | Every byte was read and all three recomputed values equal the recorded values. |
| `MISMATCH` | Every byte was read and at least one recomputed value differs. |
| `UNAVAILABLE` | The preserved bytes could not be read. No comparison was made. |

| Condition | Result |
| --- | --- |
| Unchanged | `MATCH` |
| Modified, truncated, expanded or replaced (same or different inode) | `MISMATCH` |
| Missing, unreadable, directory/symlink/FIFO in place of the file, unavailable directory or root, storage error during open or mid-read | `UNAVAILABLE` |

A storage failure is never a `MISMATCH`: a comparison is made only from a complete, successful
read, so a failure halfway through an object cannot produce a spurious difference.

### Response

All three results are `200` with `{evidence_object_id, result, byte_size, sha256, sha512,
verified_at, verified_by, message}`. `byte_size`, `sha256` and `sha512` are the **recorded** intake
values (the same fields as `EvidenceObjectOut`). `MISMATCH` adds `expected_byte_size`,
`computed_byte_size`, `expected_sha256`, `computed_sha256`, `expected_sha512`, `computed_sha512`;
`MATCH` and `UNAVAILABLE` omit them. `verified_at` equals the audit event's `occurred_at`.
The `MATCH` message is exactly "Preserved bytes match the recorded intake integrity values."

**Design decision — `UNAVAILABLE` is a result, not an HTTP error.** The three-valued result is the
domain vocabulary, and "the preserved bytes cannot be read" is itself an integrity-relevant finding
about the object rather than a failure of the verification service. Returning it as `200` keeps one
response shape, lets it be audited like the other outcomes, and avoids inviting blind retries of
what may be a missing file. Errors that are not findings about the object (authentication,
authorization, state conflicts, validation, a database failure) keep the error envelope. Retrieval,
which has no result vocabulary, reports the same storage condition as `503`. If you would rather
have `UNAVAILABLE` as a `503`, the change is confined to `verify_preserved_object` and the UI
mapping.

## Audit events

Both use `entity_type = "evidence_object"`, `entity_public_id = <EOBJ id>`, the actor's public
`USR` id, the Case and its Organization, and the request's correlation id. Details carry metadata
only.

| Action | Recorded when | `details` |
| --- | --- | --- |
| `evidence.object.retrieved` | once, on successful retrieval (before the final chunk is released) | `evidence_id`, `byte_size` (bytes delivered), `media_type`, `state` |
| `evidence.object.integrity_verified` | once per verification request that passes authorization and eligibility, for each of `MATCH`, `MISMATCH` and `UNAVAILABLE` | `evidence_id`, `result`, `expected_byte_size/sha256/sha512`, `computed_byte_size/sha256/sha512` (null for `UNAVAILABLE`) |

Denied, invalid and failed requests record no retrieval or verification event (authorization
denials continue to produce the existing `authorization.denied` security event). If the audit write
cannot be committed, verification returns an error and no result, and retrieval releases no
complete copy. Events are append-only (ORM guards on every database; triggers on PostgreSQL).

## Frontend

The existing EvidenceObject card gains a "Retrieve and verify" group for `PRESERVED` objects when
the session holds `evidence:read`. It always shows "Integrity verification is not an authenticity
determination."

- Retrieve: `Ready` → `Retrieving…` → `Completed`, or `Retrieval unavailable` with the sanitized
  message and request id. The fetch is same-origin and relative; the bytes go to the browser's
  download mechanism through a short-lived object URL and never into component state.
- Verify: `Verify` → `Verifying…` → `Integrity match`, `Integrity mismatch` (recorded and
  recomputed values side by side, differences marked in text, neutral wording) or `Verification
  unavailable`. An HTTP error (`401`, `403`, `404`, `409`, `422`, `5xx`) is shown as "Verification
  could not be completed", never as "Verification unavailable".
- The browser buffers the retrieved object as a `Blob` before saving (bounded by
  `VERITAS_MAX_EVIDENCE_BYTES`, 100 MiB by default); API clients stream.

## Failure behavior

| Failure | Retrieval | Verification |
| --- | --- | --- |
| Object missing or unreadable | `503` envelope, no event | `200 UNAVAILABLE`, one event |
| Read error on first read | `503` envelope, no event | `200 UNAVAILABLE`, one event |
| Read error mid-stream | connection aborted, no event | `200 UNAVAILABLE` (never `MISMATCH`), one event |
| Client disconnects mid-transfer | handle closed, no event, nothing mutated | not applicable (result is still audited once) |
| Database lock timeout on the audit write | no complete copy released | `500`, no result, nothing recorded; retry records exactly one event |
| Database unreachable | `500`, storage never opened | `500`, storage never opened |
| Process restart | objects, sessions, audit history unaffected | unaffected |

## Verification performed

See the handoff manifest for the recorded results. The runtime gate is reproducible with
`scripts/qa/v2_2_runtime_check.py`, which drives a disposable Compose project (never the `pgdata`
volumes of another project) through retrieval, verification, large-object streaming, tamper,
missing object, unreadable object, database outage and restart persistence over real HTTP.

## Limitations

- Integrity verification detects that bytes differ from the recorded values. It cannot say why,
  and it says nothing about the bytes before intake.
- The audit records completed retrievals only. A reader who aborts a transfer before the final
  chunk leaves no retrieval event and may have received most of the object; such attempts appear
  only in the structured access log (route, status, duration, request id). Byte-range retrieval is
  not offered.
- Verification costs a full read and two hashes of the object and is bounded only by
  authentication and the worker thread pool; there is no per-user rate limit.
- A caller with write access to the evidence volume can change stored bytes. VERITAS detects that
  on the next verification; it does not prevent it (no WORM storage in this version).
- Windows: `O_NOFOLLOW` does not exist there, so the final-component symlink refusal is not
  provided; directory-symlink and junction refusal is. Windows was not exercised.
- No external penetration test, forensic certification or legal-admissibility claim is made.
