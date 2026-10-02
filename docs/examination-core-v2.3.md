# VERITAS V2.3 — Examination Core

V2.3 activates the first real, executable examination workflow:

```
PRESERVED EvidenceObject → Method → Analysis Run → Observations → Audit
```

It is cumulative. It reuses the existing `Evidence`, `EvidenceObject`, `AnalysisRun`,
`Observation` and `AuditEvent` concepts, the private `EvidenceStorage`, the SHA-256/SHA-512
hashing primitives, the error envelope and the capability/Case authorization system. It adds
**one** Method, **one** capability (`examination:execute`) and **one** migration (`0004`). It does
not add an "Examination" entity, a queue service, a second audit system or a second role policy.

**A deterministic characteristic is only a characteristic.** An Observation records what a
Method measured. It does not determine authenticity, origin, manipulation, malware status,
truthfulness or legal admissibility, and **INTEGRITY MATCH IS NOT AUTHENTICITY PROOF.**

## 1. Distinctions that are kept exact

| Pair | Meaning |
| --- | --- |
| Lead ≠ Evidence | A lead is not evidence until it is registered and preserved. |
| Source statement ≠ Observation ≠ Inference ≠ Hypothesis ≠ Human assessment | Different epistemic kinds. |
| Observation ≠ Finding ≠ Claim ≠ Assessment | An examination publishes **Observations only**. Findings, Claims and Assessments are never created automatically. |
| Integrity match ≠ Authenticity | A match says the bytes equal the values recorded at intake; nothing more. |

No automated method may modify Evidence, an EvidenceObject, an Evidence Profile, custody
history, existing Observations, Findings, Claims, Assessments or preserved bytes.

## 2. One concept, one owner

| Concept | Owner |
| --- | --- |
| What a Method is, and what it may do | `backend/app/examination/contracts.py` |
| Which Methods exist | `backend/app/examination/registry.py` (`METHOD_REGISTRY`) |
| The first Method | `backend/app/examination/methods/binary_characteristics.py` |
| What a Method may read, and the integrity gate | `backend/app/examination/runner.py` (`EvidenceReader`) |
| Every Analysis Run state change in the database | `backend/app/examination/coordinator.py` |
| Which state changes are permitted | `backend/app/domain/lifecycle.py` (also enforced by the ORM guard and a PostgreSQL trigger) |
| Eligibility, idempotent creation, cancel, retry (API commands) | `backend/app/services/examination.py` |
| The execution record | the existing `AnalysisRun` (no `Examination` table) |
| Observations | the existing `Observation`, written by the existing `records.record_observation` |
| Audit | the existing `AuditEvent` |
| Role → capability mapping | `backend/app/domain/roles.py` |
| "These are the recorded bytes" (comparison of recomputed values with intake values) | `evidence_hashing.matches_recorded_integrity`, shared with V2.2 verification |

## 3. The Method concept

A **Method** is a versioned, code-owned, deterministic executable definition with: `key`,
`version`, `name`, `purpose`, supported evidence types, input requirements, a parameter contract
(a strict pydantic model; unknown parameters are rejected), an output contract (the Observation
kinds it publishes, in order), limitations, resource limits, `deterministic`, and `enabled`.

* Definitions live **in code**. There is no database table, flag or setting that changes what a
  Method does (no second mutable policy system).
* `GET /api/v1/cases/{case_id}/examination/methods` projects the registry. The frontend discovers
  Methods from it and defines none of its own (a repository test enforces this).
* **Immutability.** A released `(key, version)` never changes meaning; a change ships as a new
  version. `MethodDefinition.digest()` is a SHA-256 of the canonical public contract (independent
  of pydantic's generated JSON-Schema text), and a test pins the digest of
  `core.binary_characteristics@1.0`, so editing 1.0 fails the build. Golden outputs for fixed byte
  strings pin the behaviour as well.
* A Method receives **only** an `EvidenceInput` (`chunks()`), and validated parameters. It cannot
  reach a path, a storage key, a file object, the database, the network or a subprocess. The
  examination package imports none of `subprocess`, `socket`, `http`, `urllib`, `os`, `pathlib`,
  `shutil`, `tempfile` and calls none of `eval`, `exec`, `compile`, `open` or `__import__`; an AST
  test enforces this, and another proves the Method still runs with sockets and process creation
  made impossible.

### 3.1 `core.binary_characteristics@1.0` — Binary Characteristics Examination

Purpose: *calculate deterministic byte-level characteristics of a preserved EvidenceObject
without interpreting the bytes as instructions or making an authenticity judgment.* It supports
every evidence type and accepts **no** parameters.

It reads the bytes **once**, in 64 KiB chunks, keeping only a 256-bin histogram, so memory is
O(1) in the size of the evidence. All five numbers come from that histogram with these fixed
definitions (identical on every platform):

| Observation | Definition |
| --- | --- |
| `Observed byte count: N.` | Bytes read during this examination (counted independently of the recorded size). |
| `Observed Shannon byte entropy: H bits per byte.` | `H = −Σ p·log₂ p` over byte values that occur, `p = count/total`; computed as `(N·ln N − Σ c·ln c) / (N·ln 2)` with the `decimal` module at **50 significant digits** (`ln` is correctly rounded), in a private `Context` that ignores the process-wide decimal context, then rounded **half-even to 4 decimal places**. No floating point. Range 0–8. |
| `Observed printable ASCII byte ratio: R.` | Bytes `0x20`–`0x7E` inclusive over the byte count. TAB, LF and CR are **not** counted. Exact integer arithmetic, half-even, 4 places. |
| `Observed NUL-byte ratio: R.` | Bytes equal to `0x00` over the byte count. Exact integer arithmetic, half-even, 4 places. |
| `Observed distinct byte values: D of 256.` | How many of the 256 values occur at least once. |

Byte count is a plain integer (no separators). **Empty object:** byte count `0`, distinct `0 of
256`, and entropy and both ratios are published as *"not defined for an empty object"* — no
value is invented. The statements never contain conclusion vocabulary; the output contract
rejects `authentic*`, `fake*`, `forg*`, `manipulat*`, `suspicious`, `malicious` and `real` in any
statement and in Method metadata (the Method's own *limitations* are authored disclaimers that
say what is **not** determined, and are pinned verbatim by tests).

Measured on a 2-vCPU sandbox: about 29 MiB/s end to end (both integrity digests included), peak
traced Python allocations ≈ 140 KiB for a 24 MiB and for a 96 MiB object.

## 4. Analysis Run

`AnalysisRun` is the canonical execution record. Migration `0004` appends:

| Column | Purpose |
| --- | --- |
| `evidence_object_id` (NOT NULL) | The **exact** EvidenceObject examined. |
| `idempotency_key` (nullable), `request_fingerprint` (NOT NULL) | Server-side idempotency; the fingerprint is a SHA-256 of the canonical, validated execution request. |
| `cancel_requested_at`, `last_heartbeat_at` | Cancellation and liveness. |
| `failure_code`, `failure_message` | Failure class and one fixed, sanitized sentence per code. |

Existing columns are unchanged. `started_at` is the start of the current claim and doubles as the
fencing token; `completed_at` is the time the run reached a terminal state (completed, failed or
cancelled).

**Exact provenance is enforced by the database:** a composite foreign key
`(evidence_object_id, evidence_id, case_id) → evidence_objects(id, evidence_id, case_id)` (backed
by a unique index on `evidence_objects`) means no run can name an object of another Evidence item
or Case, even if service code were wrong. The service checks the same relationships first and
returns a clear 404.

**Public identifiers only** (`ANL-001`, `EOBJ-001`, `EVD-001`, `OBS-001`); internal UUIDs never
leave the backend.

### 4.1 Lifecycle

```
QUEUED ──► RUNNING ──► COMPLETED
   │          ├──────► FAILED
   │          ├──────► CANCELLED
   └──────────┴──────► CANCELLED          RUNNING ──► QUEUED  (only: stale-worker recovery, graceful release)
```

Terminal states are final. There is no other transition. A **retry is a new run**; a failed run is
never mutated into a retry and its public id is never reused. The rules are enforced three times:
`domain/lifecycle.py` (before any compare-and-set is issued), an ORM `before_update` guard (all
databases), and — on PostgreSQL — a trigger that also forbids deleting a run, changing what it
was asked to do (case, evidence, object, method, version, parameters, key, fingerprint,
creation), and changing a finished run. `CHECK` constraints keep state and timestamps consistent
(e.g. a `completed` run has `completed_at` and no cancellation request; a `failed` run has a code
and message; a `queued` run has no start). SQLite, used only for tests, has the ORM guard and the
CHECKs but not the trigger; tests state this and skip the trigger tests there.

## 5. Eligibility

Order of events for `POST …/analysis-runs` (and retry):

1. **Route authorization first** — authenticated principal, Case readable (masked 404 otherwise),
   `examination:execute`, **not a demonstration Case**, CSRF. No storage is touched yet.
2. Service defence in depth: demonstration Case refused again.
3. Evidence belongs to the Case; EvidenceObject belongs to that Evidence and Case → 404.
4. EvidenceObject is `PRESERVED` → `409 evidence_object_not_preserved`. There is no quarantine or
   alternate-location fallback.
5. Method and version exist and are enabled → `409 method_unavailable`.
6. Method supports the Evidence type (and the object fits its size limit) → `409 method_inapplicable`.
7. Parameters satisfy the Method's contract → `422 invalid_method_parameters` (rule types only; no
   submitted key or value is echoed).
8. Idempotency decision (§6).
9. The object is **opened and closed, never read**, to confirm it is readable →
   `503 evidence_storage_unavailable`.
10. One `QUEUED` run and one `examination.run.created` event are committed.

The same checks run again when a worker starts the run (the Method may have been withdrawn, the
object ineligible, the Case flagged): the run then **fails closed** with the matching code.

## 6. Idempotency

The request carries `idempotency_key` (8–128 characters of `A–Z a–z 0–9 . _ : -`). A unique
database constraint on `(case_id, created_by, idempotency_key)` makes the decision, so a double
click, a browser retry or twelve concurrent identical requests create exactly one run.

| Same principal + Case + key, and … | Result |
| --- | --- |
| an equivalent request | `200` and the **original** run (a new run is `201`) |
| a different request | `409 idempotency_conflict`, nothing created |

Equivalence is the SHA-256 of the canonical, *validated* request (Case, Evidence, EvidenceObject,
Method key and version, normalized parameters, and — for a retry — the source run). A replay does
not touch storage, so it still succeeds if storage is down. The idempotency key and fingerprint are
never returned or logged. The UI keeps **one key per intent**: it reuses the key only while the
outcome of the previous attempt is unknown (connection failure) and rotates it after any answer.

## 7. Execution

A run is queued by the request and executed later by a worker, **outside the request
transaction**. The queue is the `analysis_runs` table; there is no Redis, Celery, RabbitMQ or
Kafka. `ExaminationSupervisor` is an in-process daemon thread started by the application
lifespan (`VERITAS_EXAMINATION_WORKER_ENABLED`, default on); the API wakes it when a run is queued.
Several processes are safe — every claim is a database compare-and-set.

* **Claim.** `UPDATE … WHERE state='queued'` (plus `FOR UPDATE SKIP LOCKED` on PostgreSQL). Exactly
  one worker wins a run. The claim stamps `started_at` and `last_heartbeat_at` and writes
  `examination.run.started` in the same transaction.
* **Fencing.** Every later write of that worker repeats `started_at = <its claim>` in the `WHERE`.
  A worker that stalled and lost its run to recovery can no longer heartbeat, complete, fail,
  cancel or publish — its writes match no row.
* **Heartbeat.** Between chunks, at most every `VERITAS_EXAMINATION_HEARTBEAT_SECONDS`, the worker
  refreshes `last_heartbeat_at` and learns of a cancellation request in one short transaction. No
  transaction is open while bytes are read.
* **Atomic publication.** `RUNNING → COMPLETED`, all Observations (through
  `records.record_observation`, origin `analysis_run`, `analysis_run_id` set) and the audit events
  are **one transaction**: all or nothing. A failed run leaves no Observation that could look like
  a result.
* **Cancellation.** `QUEUED` is cancelled immediately. For `RUNNING` the API only sets
  `cancel_requested_at` (and audits it); the worker stops at its next checkpoint and records
  `CANCELLED`. Completion requires `cancel_requested_at IS NULL`, so the race is decided by commit
  order on one row: a cancelled run can never become completed and a completed run can never
  become cancelled (the cancel call then returns `409 invalid_lifecycle_transition`). A *failure*
  that commits while a cancellation is pending is recorded as `FAILED` — the failure is real — and
  `cancel_requested_at` stays in the record.
* **Retry.** `POST …/retry` (failed or cancelled runs only) copies the intent — Evidence,
  EvidenceObject, Method key/version, validated parameters — into a **new** run with its own
  identifier, key, timestamps, lifecycle and `examination.run.retried` event. Eligibility is
  re-checked in full. The old run is untouched.
* **Recovery.** A `RUNNING` run whose heartbeat is older than `VERITAS_EXAMINATION_STALE_SECONDS`
  (default 30; must be at least three times the heartbeat interval) returns to `QUEUED` — or to
  `CANCELLED` if cancellation was requested — and an `examination.run.recovered` event is written.
  The compare-and-set re-checks staleness, so a healthy worker's run is never taken. On graceful
  shutdown the worker releases its run to the queue immediately; after a hard crash the run
  recovers once its heartbeat is stale. A queued run survives any restart because it is a row.
* **Database failure** while executing records nothing (the heartbeat goes stale and recovery
  requeues); the worker loop survives any single failure.

Settings (all `VERITAS_*`): `EXAMINATION_WORKER_ENABLED` (true), `EXAMINATION_POLL_SECONDS` (1.0),
`EXAMINATION_HEARTBEAT_SECONDS` (2.0), `EXAMINATION_STALE_SECONDS` (30.0).

## 8. The controlled evidence reader and the integrity gate

The Method is handed an `EvidenceReader`, never a path. It yields the **PRESERVED** bytes once,
at most `chunk_bytes` (64 KiB) at a time, via `EvidenceStorage.open_preserved_object`; it calls the
checkpoint (heartbeat, cancellation, shutdown) before every chunk, enforces the Method's byte and
wall-clock limits, and feeds the canonical `StreamingDigest`. When the stream is exhausted the
bytes read are compared (`matches_recorded_integrity`) with the byte count, SHA-256 and SHA-512
recorded at intake. **A mismatch fails the run** (`integrity_mismatch`) and publishes nothing, so
Observations exist only for bytes that still equal the recorded PRESERVED object. A Method that
does not read the whole object, or that swallows a read failure, also cannot publish. This is an
integrity comparison, not an authenticity statement; the side-by-side comparison remains V2.2
verification. Examination writes nothing to storage and creates no temporary or duplicate copy
(a test forbids `tempfile` and `shutil` copies).

## 9. Authorization

`examination:read` — INVESTIGATOR, REVIEWER, RESEARCHER, AUDITOR (existing).
`examination:execute` — **INVESTIGATOR and RESEARCHER only** (new). REVIEWER and AUDITOR read
only; CUSTODIAN and ADMINISTRATOR have neither; an Administrator's identity privileges never imply
Case examination; the anonymous demonstration viewer can read (empty) demonstration lists and
Methods but cannot execute. The map lives only in `domain/roles.py`; a test proves the string
`examination:execute` appears in exactly two code sites (the map and the route guard).

All mutating routes use `require_case_capability("examination:execute", authenticated_only=True,
non_demonstration_only=True)`: Case authorization, masked 404 with an `authorization.denied`
audit event, CSRF — all before any storage access. A test with a recording storage proves
unauthorized requests, and authorized-but-ineligible requests, never open an object.

## 10. Audit

Only the existing `AuditEvent` is used. Entity: `analysis_run`; actor: the user for user actions,
`system:examination-worker` for worker actions; `request_id` correlates (`examination-<ANL-id>` for
worker events). `details` carry identifiers and state only — **never** bytes, observation text,
paths, storage keys, idempotency keys, fingerprints, secrets or stack traces. Every event has at
least `case_id`, `evidence_id`, `evidence_object_id`, `method_key`, `method_version`, `state`.

| Action | When | Extra details |
| --- | --- | --- |
| `examination.run.created` | run queued | — |
| `examination.run.started` | worker claimed it | — |
| `examination.run.completed` | published atomically | `observation_count` |
| `examination.run.failed` | worker recorded failure | `failure_code` |
| `examination.run.cancelled` | queued run cancelled, or worker finished a requested cancellation | `reason` |
| `examination.run.retried` | new run created by retry | `source_run_id` |
| `examination.run.cancel_requested` | cancellation requested for a running run | — |
| `examination.run.recovered` | running run returned to the queue | `reason` |

The last two are additions beyond the six actions named in the V2.3 brief: without them a user's
cancellation request and a system requeue would be unaudited state changes. Each Observation also
produces the existing `observation.recorded` event.

## 11. API

| Method and path | Capability | Result |
| --- | --- | --- |
| `GET /api/v1/cases/{case_id}/examination/methods` | `examination:read` | registered Methods |
| `GET /api/v1/cases/{case_id}/analysis-runs` | `examination:read` | runs, each with its exact EvidenceObject (existing route, extended) |
| `POST /api/v1/cases/{case_id}/analysis-runs` | `examination:execute` | `201` new run, `200` replay |
| `GET /api/v1/cases/{case_id}/analysis-runs/{run_id}` | `examination:read` | run + its Observations |
| `POST /api/v1/cases/{case_id}/analysis-runs/{run_id}/cancel` | `examination:execute` | `200` cancelled, `202` requested |
| `POST /api/v1/cases/{case_id}/analysis-runs/{run_id}/retry` | `examination:execute` | `201` new run, `200` replay |

Run fields: `id, evidence_id, evidence_object_id, method_key, method_version, state, parameters,
started_at, completed_at, cancel_requested_at, last_heartbeat_at, failure_code, failure_message,
created_by, created_at, updated_at`; the detail adds `observations` (`id, statement, origin,
analysis_run_id, evidence_id, evidence_label, recorded_by, created_at`). No route accepts a path,
a storage key or a root.

### Failure semantics

Each failure is distinct and uses the existing error envelope
`{"error":{"code","message","request_id"}}`.

| Situation | Status / code |
| --- | --- |
| Resource or Case not found, or not authorized (masked) | `404 not_found` |
| Not authenticated | `401` |
| Missing/invalid CSRF token | `403 access_denied` |
| Invalid request shape | `422 validation_error` |
| Parameters violate the contract | `422 invalid_method_parameters` |
| Method unknown, version unknown or disabled | `409 method_unavailable` |
| Method does not support the evidence | `409 method_inapplicable` |
| EvidenceObject not PRESERVED | `409 evidence_object_not_preserved` |
| Idempotency key reused for a different request | `409 idempotency_conflict` |
| Run cannot change state (cancel a finished run; retry a non-failed run) | `409 invalid_lifecycle_transition` |
| Private storage cannot be read at request time | `503 evidence_storage_unavailable` |

At execution time a run ends `FAILED` with `failure_code` ∈ `method_unavailable`, `not_eligible`,
`evidence_unavailable`, `integrity_mismatch`, `resource_limit_exceeded`, `execution_failed`, each
with one fixed message. A storage failure is never reported as a successful run and a failed run is
never reported as completed. A failed Method is never silently retried inside the same run.

## 12. Frontend

`features/examination/` replaces the reserved page with one workstation, using the existing
navigation, design system and state components:

* **Explain** — the Methods the backend returns, each with an expandable explanation (what it
  publishes, what it needs, limitations, limits) built only from backend metadata.
* **Examine** — choose an EvidenceObject (Evidence ID, EvidenceObject ID, filename, state, size,
  whether integrity values are recorded; only `PRESERVED` objects are selectable) and start. The
  button states the exact reason it is unavailable.
* **Trace** — runs, newest first; the selected run shows its real state, exact Method/version,
  Evidence, EvidenceObject, parameters, requester and timestamps, its Observations, and Cancel /
  Retry where applicable. State is polled from the backend (only while a run is active) and shown
  as received; if a refresh fails the UI says so and keeps the last state. There is no simulated
  progress.
* **Verify** — a pointer to integrity verification on the Evidence page. **Review** — a note and a
  legend that keep Observation, Finding, Claim and Assessment apart.

Availability comes from `/api/v1/system`; permissions from the session. Keyboard operable, labelled,
state announced in a live region, focus moves to a newly started run; reflows to one column on small
screens. Timeline, Review and Report remain reserved.

## 13. Migration `0004_examination_core`

Append-only (no earlier migration was edited; a test pins their hashes with line endings
normalized). `analysis_runs` is empty in every supported deployment — no V1–V2.2 code path ever
created a run — so the upgrade **refuses to run if it finds rows** instead of guessing a provenance.
Upgrade/downgrade/upgrade is tested on SQLite and PostgreSQL, as is the CI sequence (upgrade, seed
twice, downgrade to base). The downgrade is destructive for V2.3 provenance (the columns are
dropped); take a backup first — the repository only downgrades disposable databases.

## 14. What V2.3 does **not** claim

It does not determine authenticity, origin, manipulation, malware status, truthfulness or legal
admissibility; it does not interpret bytes as instructions; it creates no Findings, Claims or
Assessments, no scores or probabilities, and uses no AI or external service. It is not a forensic
certification, and no external security assessment was performed.

## 15. Limitations

* One in-process worker thread per backend process; throughput is a single Python thread's. There
  is no per-user queue quota or rate limit.
* A run recovered after a hard crash waits up to the stale threshold; a run that crashes its worker
  every time would be retried each time (no attempt counter).
* Clock skew between hosts shifts stale detection by the skew (use NTP; the threshold defaults to
  30 s).
* The method is pure Python (about 29 MiB/s measured). Objects near the 1 GiB limit take minutes.
* SQLite (tests only) has no database trigger for run history; PostgreSQL does.
* Windows is not exercised here; the code and tests avoid POSIX-only attributes and skip honestly,
  but they were run on Linux.
