"""V2.3 runtime verification against a DISPOSABLE Docker Compose project (not part of check.sh).

Builds and starts the shipped stack under its own ``veritas-v23-*`` project and proves, over real
HTTP through nginx and against real PostgreSQL, that an authorized examination really happens:

    PRESERVED EvidenceObject -> core.binary_characteristics@1.0 -> Analysis Run -> Observations

* the full acceptance flow (queued -> running -> completed, Observations with exact provenance,
  audit trail, no Finding/Claim/Assessment, EvidenceObject, custody and stored bytes unchanged);
* authorization over HTTP, CSRF, demonstration Cases, idempotency and duplicate requests;
* a 96 MiB object examined with a measured, bounded memory footprint;
* cancellation of a queued and of a running run; retry as a NEW run;
* real failure injection: tampered bytes, a missing and an unreadable object, truncation while a
  run is executing, a database outage, a graceful restart and a hard kill (SIGKILL) of the backend.

Every expected Observation is computed here, independently of the application code.

Safety: the project name must match ``veritas-v23-*``. The script refuses any other name, never
names the volumes of another project, removes only this project's containers and volumes, and
checks that ``veritas_pgdata`` / ``veritas-v2_pgdata`` are untouched (a protected volume that does
not exist on the host is reported as such: the check then shows only that none was created).
Nothing is written to the repository; the generated database password and the override file live
in a temporary directory.

    python3 scripts/qa/v2_3_runtime_check.py            # build, run, verify, tear down
    python3 scripts/qa/v2_3_runtime_check.py --keep     # leave the stack running afterwards

Requires Docker with the Compose plugin and permission to use it. Port 8080 on 127.0.0.1 must be
free. Exit status is non-zero if any check fails.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import secrets
import shutil
import sys
import tempfile
import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import v2_2_runtime_check as base

ROOT = base.ROOT
PROJECT_PATTERN = re.compile(r"^veritas-v23-[a-z0-9-]{1,30}$")
MIB = base.MIB
METHOD = {"method_key": "core.binary_characteristics", "method_version": "1.0"}
LARGE_BYTES = 96 * MIB
MEDIUM_BYTES = 64 * MIB
MEMORY_GROWTH_LIMIT = 40 * MIB  # a buffering implementation would grow by about the object size
PRINTABLE = range(0x20, 0x7F)


# --- Independent oracle ------------------------------------------------------------------------


class HistogramBody(base.PatternBody):
    """A request body that also keeps a client-side byte histogram of what it sent."""

    def __init__(self, size: int) -> None:
        super().__init__(size)
        self.histogram = [0] * 256

    def read(self, amount: int = -1) -> bytes:
        block = super().read(amount)
        for value, occurrences in Counter(block).items():
            self.histogram[value] += occurrences
        return block


def ratio(count: int, total: int) -> str:
    scaled = round(Fraction(count * 10_000, total))  # exact; Python rounds a tie to even
    return f"{scaled // 10_000}.{scaled % 10_000:04d}"


def expected_statements(histogram: list[int]) -> list[str]:
    total = sum(histogram)
    distinct = sum(1 for c in histogram if c)
    if total == 0:
        return [
            "Observed byte count: 0.",
            "Observed Shannon byte entropy: not defined for an empty object.",
            "Observed printable ASCII byte ratio: not defined for an empty object.",
            "Observed NUL-byte ratio: not defined for an empty object.",
            "Observed distinct byte values: 0 of 256.",
        ]
    entropy = -math.fsum((c / total) * math.log2(c / total) for c in histogram if c)
    printable = sum(histogram[v] for v in PRINTABLE)
    return [
        f"Observed byte count: {total}.",
        f"Observed Shannon byte entropy: {entropy:.4f} bits per byte.",
        f"Observed printable ASCII byte ratio: {ratio(printable, total)}.",
        f"Observed NUL-byte ratio: {ratio(histogram[0], total)}.",
        f"Observed distinct byte values: {distinct} of 256.",
    ]


def histogram_of(data: bytes) -> list[int]:
    counts = [0] * 256
    for value, occurrences in Counter(data).items():
        counts[value] = occurrences
    return counts


def statements_match(observed: list[str], expected: list[str]) -> bool:
    """Exact, except entropy, which may differ from the float oracle by 4-place rounding."""
    if len(observed) != len(expected):
        return False
    for got, want in zip(observed, expected, strict=True):
        if "entropy" in want and "not defined" not in want:
            a, b = (
                re.findall(r"[0-9]+\.[0-9]{4}", got),
                re.findall(r"[0-9]+\.[0-9]{4}", want),
            )
            if len(a) != 1 or len(b) != 1 or abs(float(a[0]) - float(b[0])) > 0.00011:
                return False
        elif got != want:
            return False
    return True


# --- Stack --------------------------------------------------------------------------------------


class Stack23(base.Stack):
    """The disposable project, with examination timings short enough to observe recovery."""

    def __init__(self, project: str, workdir: Path) -> None:
        self.project = project
        self.override = workdir / "override.yml"
        self.envfile = workdir / ".env"
        self.override.write_text(
            "services:\n  backend:\n    environment:\n"
            "      VERITAS_ACCESS_MODE: restricted\n"
            '      VERITAS_EXAMINATION_POLL_SECONDS: "0.5"\n'
            '      VERITAS_EXAMINATION_HEARTBEAT_SECONDS: "1.0"\n'
            '      VERITAS_EXAMINATION_STALE_SECONDS: "6.0"\n'
        )
        self.envfile.write_text(f"POSTGRES_PASSWORD={secrets.token_urlsafe(24)}\n")
        self.envfile.chmod(0o600)


@dataclass
class Ctx(base.Ctx):
    """Shared state: the base context plus the objects and runs this check creates."""

    medium: bytes = b""
    medium_hist: list[int] = field(default_factory=list)
    medium_evidence: str = ""
    medium_object: str = ""
    large_evidence: str = ""
    large_object: str = ""
    large_hist: list[int] = field(default_factory=list)
    large_target: str = ""
    spare_evidence: str = ""
    spare_object: str = ""
    spare_hist: list[int] = field(default_factory=list)
    spare_target: str = ""
    demo_case: str = "CASE-001"
    quarantined_object: str = ""
    used_keys: list[str] = field(default_factory=list)

    # -- addressing -----------------------------------------------------------------------------

    def runs(self, suffix: str = "") -> str:
        return f"/api/v1/cases/{self.case}/analysis-runs{suffix}"

    def object_base(self, evidence_id: str, object_id: str) -> str:
        return f"/api/v1/cases/{self.case}/evidence/{evidence_id}/objects/{object_id}"

    def storage_path(self, object_id: str) -> str:
        """Absolute path of a preserved object inside the backend container (kept local)."""
        out = self.stack.exec(
            "python",
            "-c",
            "from app.core.config import get_settings\n"
            "from app.db.session import build_engine, build_session_factory\n"
            "from app.domain.models import EvidenceObject\n"
            "from sqlalchemy import select\n"
            "f = build_session_factory(build_engine(get_settings()))\n"
            "with f() as s:\n"
            "    query = select(EvidenceObject.storage_key)\n"
            f"    query = query.where(EvidenceObject.public_id == '{object_id}')\n"
            "    key = s.execute(query).scalar_one()\n"
            f"    print('{base.EVIDENCE_DIR}/preserved/' + key + '.bin')\n",
        )
        return out.stdout.decode().strip().splitlines()[-1]

    # -- runs ----------------------------------------------------------------------------------

    def start(
        self,
        client: str,
        evidence_id: str,
        object_id: str,
        key: str | None = None,
        **extra: Any,
    ) -> tuple[int, dict[str, Any]]:
        chosen = key or f"rt-{secrets.token_hex(10)}"
        self.used_keys.append(chosen)
        body = {
            "evidence_id": evidence_id,
            "evidence_object_id": object_id,
            **METHOD,
            "parameters": {},
            "idempotency_key": chosen,
            **extra,
        }
        return self.clients[client].json("POST", self.runs(), body=body)

    def retry(self, run_id: str, key: str) -> tuple[int, dict[str, Any]]:
        self.used_keys.append(key)
        return self.clients["investigator"].json(
            "POST", self.runs(f"/{run_id}/retry"), body={"idempotency_key": key}
        )

    def limit_backend_cpu(self, cpus: str) -> bool:
        """Throttle (or, with "0", unthrottle) the backend container's CPU; ``False`` if refused."""
        result = base.subprocess.run(
            ["docker", "update", f"--cpus={cpus}", self.stack.container("backend")],
            capture_output=True,
            check=False,
        )
        return result.returncode == 0

    def run_count(self) -> int:
        return len(self.clients["investigator"].json("GET", self.runs())[1]["items"])

    def detail(self, run_id: str, client: str = "investigator") -> dict[str, Any]:
        status, body = self.clients[client].json("GET", self.runs(f"/{run_id}"))
        if status != 200:
            raise RuntimeError(f"run {run_id} unreadable: {status} {body}")
        return body

    def wait_for(
        self,
        run_id: str,
        states: set[str],
        timeout: float = 180,
        client: str = "investigator",
    ) -> tuple[dict[str, Any], list[str]]:
        """Poll until the run is in one of ``states``; also returns the distinct states seen."""
        seen: list[str] = []
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            current = self.detail(run_id, client)
            if not seen or seen[-1] != current["state"]:
                seen.append(current["state"])
            if current["state"] in states:
                return current, seen
            time.sleep(0.1)
        raise RuntimeError(f"run {run_id} never reached {states}; saw {seen}")

    def actions_of(self, entity: str) -> list[tuple[str, str]]:
        _, body = self.clients["investigator"].json(
            "GET", f"/api/v1/cases/{self.case}/audit-events?limit=500"
        )
        rows = [e for e in body.get("items", []) if e.get("entity_id") == entity]
        rows.sort(key=lambda e: (e["occurred_at"], e["id"]))
        return [(str(e["action"]), str(e["actor"])) for e in rows]

    def case_counts(self) -> dict[str, int]:
        _, body = self.clients["investigator"].json("GET", f"/api/v1/cases/{self.case}")
        return dict(body["counts"])

    def intake_snapshot(self, evidence_id: str, object_id: str) -> str:
        inv = self.clients["investigator"]
        _, detail = inv.json("GET", f"/api/v1/cases/{self.case}/evidence/{evidence_id}/intake")
        _, custody = inv.json("GET", f"{self.object_base(evidence_id, object_id)}/custody")
        return json.dumps([detail, custody], sort_keys=True)

    def sha256_in_container(self, path: str) -> str:
        out = self.stack.exec("sh", "-c", f"sha256sum {path}", user="veritas").stdout.decode()
        return out.split()[0]

    def upload_object(self, body: base.PatternBody | bytes, size: int) -> tuple[str, str]:
        return self.preserve(body, size)

    def upload_bytes(self, data: bytes, *, finalize: bool = True) -> tuple[str, str]:
        """Preserve a small object; the size is the body's, so they can never disagree."""
        return self.preserve(data, len(data), finalize=finalize)


# --- Phases --------------------------------------------------------------------------------------


def phase_bring_up(ctx: Ctx) -> None:
    stack = ctx.stack
    config = json.loads(stack.run("config", "--format", "json").stdout)
    services = config["services"]
    backend_mounts = [v["target"] for v in services["backend"].get("volumes", [])]
    ctx.check(
        "project is disposable and named veritas-v23-*",
        PROJECT_PATTERN.fullmatch(config["name"]) is not None,
        config["name"],
    )
    ctx.check(
        "compose: only backend mounts the evidence volume",
        backend_mounts == [base.EVIDENCE_DIR] and not services["web"].get("volumes"),
    )
    published = [name for name, service in services.items() if service.get("ports")]
    ctx.check("compose: only nginx publishes a port", published == ["web"], str(published))
    env = services["backend"]["environment"]
    ctx.check(
        "examination timings are short enough to observe recovery",
        env.get("VERITAS_EXAMINATION_STALE_SECONDS") == "6.0",
    )
    stack.run("down", "-v", "--remove-orphans", check=False)
    stack.run("up", "-d", "--build", "--wait")
    health = {
        name: base.docker(
            "inspect", "-f", "{{.State.Health.Status}}", stack.container(name)
        ).strip()
        for name in ("db", "backend", "web")
    }
    ctx.check(
        "db, backend and nginx are healthy",
        set(health.values()) == {"healthy"},
        str(health),
    )
    ctx.check("GET /ready through nginx (database ok, schema ok)", base.wait_ready())
    _, system = base.Client().json("GET", "/api/v1/system")
    ctx.check(
        "database schema is at revision 0004",
        system["database"]["schema_revision"] == "0004" == system["database"]["expected_revision"],
        str(system["database"]),
    )
    capabilities = {c["key"]: c["status"] for c in system["capabilities"]}
    ctx.check(
        "the backend declares examination available; timeline, review and report stay reserved",
        capabilities["examination"] == "available"
        and {capabilities[k] for k in ("timeline", "review", "report")} == {"reserved"},
        str(capabilities),
    )
    logs = base.docker("logs", stack.container("backend"), check=False)
    ctx.check(
        "the backend started its examination worker",
        "examination_worker_started" in logs,
    )
    mounts = json.loads(base.docker("inspect", "-f", "{{json .Mounts}}", stack.container("web")))
    ctx.check("the running nginx container mounts nothing", mounts == [])


def phase_provision(ctx: Ctx) -> None:
    script = (
        "from app.core.config import get_settings\n"
        "from app.db.session import build_engine, build_session_factory\n"
        "from app.services.records import create_case\n"
        "factory = build_session_factory(build_engine(get_settings()))\n"
        "with factory() as s, s.begin():\n"
        "    case = create_case(s, actor='system:runtime-check', summary=None,\n"
        "                       title='Synthetic V2.3 runtime Case', is_demonstration=False)\n"
        "    print(case.public_id)\n"
    )
    ctx.case = ctx.stack.exec("python", "-c", script).stdout.decode().strip().splitlines()[-1]
    accounts = {
        "investigator": ("INVESTIGATOR", ctx.case),
        "researcher": ("RESEARCHER", ctx.case),
        "reviewer": ("REVIEWER", ctx.case),
        "custodian": ("CUSTODIAN", ctx.case),
        "auditor": ("AUDITOR", None),
        "administrator": ("ADMINISTRATOR", None),
        "demo_investigator": ("INVESTIGATOR", ctx.demo_case),
    }
    for name, (role, scope) in accounts.items():
        command = [
            "python",
            "-m",
            "app.provision",
            "--username",
            f"{name}-{ctx.suffix}",
        ]
        command += ["--display-name", f"Runtime {name}", "--role", role]
        if scope:
            command += ["--case-id", scope]
        result = ctx.stack.exec(
            *command, stdin=f"{base.PASSWORD}\n{base.PASSWORD}\n".encode(), check=False
        )
        ctx.check(f"provisioned {role} ({name})", result.returncode == 0)
        ctx.clients[name] = base.Client()
        ctx.clients[name].login(f"{name}-{ctx.suffix}")
    ctx.clients["anonymous"] = base.Client()


def phase_end_to_end(ctx: Ctx) -> None:
    """The acceptance flow: a real examination from a PRESERVED EvidenceObject to Observations."""
    inv = ctx.clients["investigator"]
    ctx.medium = base.PDF_PREFIX + b"Synthetic V2.3 runtime bytes " + secrets.token_bytes(300_000)
    ctx.medium_hist = histogram_of(ctx.medium)
    ctx.medium_evidence, ctx.medium_object = ctx.upload_object(ctx.medium, len(ctx.medium))
    target = ctx.storage_path(ctx.medium_object)
    ctx.check("a real PRESERVED EvidenceObject exists in a non-demonstration Case", True)

    _, methods = inv.json("GET", f"/api/v1/cases/{ctx.case}/examination/methods")
    ctx.check(
        "the Method list is served by the backend registry",
        [(m["key"], m["version"]) for m in methods["items"]]
        == [("core.binary_characteristics", "1.0")],
    )

    counts_before = ctx.case_counts()
    intake_before = ctx.intake_snapshot(ctx.medium_evidence, ctx.medium_object)
    file_before = ctx.sha256_in_container(target)

    status, run = ctx.start(
        "investigator", ctx.medium_evidence, ctx.medium_object, "e2e-key-000001"
    )
    ctx.check(
        "POST analysis-run returns 201 QUEUED",
        status == 201 and run["state"] == "queued",
        str(run),
    )
    ctx.check(
        "the run names the exact EvidenceObject and Method",
        run["evidence_object_id"] == ctx.medium_object
        and run["evidence_id"] == ctx.medium_evidence
        and (run["method_key"], run["method_version"]) == ("core.binary_characteristics", "1.0"),
    )
    done, seen = ctx.wait_for(run["id"], {"completed", "failed", "cancelled"})
    ctx.check(
        "a worker claimed it and it COMPLETED",
        done["state"] == "completed",
        " -> ".join(seen),
    )
    ctx.check(
        "the observed lifecycle only moved forward and ended completed",
        seen[0] in {"queued", "running"} and seen[-1] == "completed" and "failed" not in seen,
        " -> ".join(seen),
    )
    ctx.check(
        "started, last heartbeat and completed timestamps are recorded",
        bool(done["started_at"] and done["last_heartbeat_at"] and done["completed_at"]),
    )
    observations = done["observations"]
    ctx.check(
        "five Observations, origin analysis_run, pointing at this exact run",
        len(observations) == 5
        and {o["origin"] for o in observations} == {"analysis_run"}
        and {o["analysis_run_id"] for o in observations} == {run["id"]}
        and {o["evidence_id"] for o in observations} == {ctx.medium_evidence},
    )
    statements = [o["statement"] for o in observations]
    expected = expected_statements(ctx.medium_hist)
    ctx.check(
        "the Observations equal an independent computation over the bytes that were uploaded",
        statements_match(statements, expected),
        f"{statements[0]} / {statements[1]}",
    )
    ctx.check(
        "the Observations are attributed to the system worker, not to a person",
        {o["recorded_by"] for o in observations} == {"system:examination-worker"},
    )

    trail = ctx.actions_of(run["id"])
    ctx.check(
        "audit: created, started, completed - once each, in order",
        [a for a, _ in trail]
        == [
            "examination.run.created",
            "examination.run.started",
            "examination.run.completed",
        ],
        str(trail),
    )
    ctx.check(
        "audit actors: the user for created, the system worker for started and completed",
        trail[0][1].startswith("USR-")
        and trail[1][1] == trail[2][1] == "system:examination-worker",
    )

    counts_after = ctx.case_counts()
    ctx.check(
        "no Finding, Claim, Assessment or relationship was created",
        all(
            counts_after[k] == counts_before[k]
            for k in ("findings", "claims", "assessments", "relationships")
        ),
    )
    ctx.check(
        "exactly one run and five Observations were added",
        counts_after["analysis_runs"] == counts_before["analysis_runs"] + 1
        and counts_after["observations"] == counts_before["observations"] + 5,
    )
    ctx.check(
        "the EvidenceObject and its custody history are unchanged",
        ctx.intake_snapshot(ctx.medium_evidence, ctx.medium_object) == intake_before,
    )
    ctx.check(
        "the preserved bytes are unchanged (sha256 in the volume)",
        ctx.sha256_in_container(target) == file_before,
    )
    status, verified = inv.json(
        "POST", f"{ctx.object_base(ctx.medium_evidence, ctx.medium_object)}/verify"
    )
    ctx.check(
        "V2.2 verification still reports MATCH",
        status == 200 and verified.get("result") == "MATCH",
    )
    response, data, _ = inv.request(
        "GET", f"{ctx.object_base(ctx.medium_evidence, ctx.medium_object)}/content"
    )
    ctx.check(
        "V2.2 retrieval still returns the exact bytes",
        response.status == 200 and data == ctx.medium,
    )
    ctx.base, ctx.object_id, ctx.target, ctx.small = (
        ctx.object_base(ctx.medium_evidence, ctx.medium_object),
        ctx.medium_object,
        target,
        ctx.medium,
    )


def phase_authorization(ctx: Ctx) -> None:
    ev, ob = ctx.medium_evidence, ctx.medium_object
    for role in ("researcher",):
        status, run = ctx.start(role, ev, ob)
        ctx.check(f"{role} may queue an examination", status == 201, str(status))
        ctx.wait_for(run["id"], {"completed", "failed"}, client=role)
    for role in ("reviewer", "auditor"):
        status, _ = ctx.clients[role].json("GET", ctx.runs())
        ctx.check(f"{role} may read analysis runs", status == 200, str(status))
        status, body = ctx.start(role, ev, ob)
        ctx.check(
            f"{role} cannot execute (masked 404)",
            status == 404 and body["error"]["code"] == "not_found",
            str(status),
        )
    for role in ("custodian", "administrator"):
        status, _ = ctx.clients[role].json("GET", ctx.runs())
        ctx.check(f"{role} cannot even read analysis runs", status == 404, str(status))
        status, _ = ctx.start(role, ev, ob)
        ctx.check(f"{role} cannot execute", status == 404, str(status))
    status, body = ctx.start("anonymous", ev, ob)
    ctx.check("an unauthenticated request is 401", status == 401, str(status))
    inv = ctx.clients["investigator"]
    status, body = inv.json(
        "POST",
        ctx.runs(),
        body={
            "evidence_id": ev,
            "evidence_object_id": ob,
            **METHOD,
            "parameters": {},
            "idempotency_key": "csrf-key-0000001",
        },
        csrf=False,
    )
    ctx.check(
        "a mutation without the CSRF token is 403",
        status == 403 and body["error"]["code"] == "access_denied",
        str(status),
    )
    status, body = ctx.clients["demo_investigator"].json(
        "POST",
        f"/api/v1/cases/{ctx.demo_case}/analysis-runs",
        body={
            "evidence_id": "EVD-001",
            "evidence_object_id": "EOBJ-001",
            **METHOD,
            "parameters": {},
            "idempotency_key": "demo-key-0000001",
        },
    )
    ctx.check(
        "a demonstration Case never executes a Method (masked 404)",
        status == 404,
        str(status),
    )
    unknown = ctx.start("investigator", ev, ob, method_key="core.nonexistent")
    ctx.check(
        "an unknown Method is 409 method_unavailable",
        unknown[0] == 409 and unknown[1]["error"]["code"] == "method_unavailable",
    )
    bad = ctx.start("investigator", ev, ob, parameters={"x": 1})
    ctx.check(
        "invalid parameters are 422 invalid_method_parameters",
        bad[0] == 422 and bad[1]["error"]["code"] == "invalid_method_parameters",
    )
    quarantined_ev, quarantined_ob = ctx.upload_bytes(
        base.PDF_PREFIX + b"quarantined", finalize=False
    )
    status, body = ctx.start("investigator", quarantined_ev, quarantined_ob)
    ctx.check(
        "a QUARANTINED object cannot be examined (409)",
        status == 409 and body["error"]["code"] == "evidence_object_not_preserved",
        str(status),
    )
    status, body = ctx.start("investigator", ev, "EOBJ-999")
    ctx.check("a guessed EvidenceObject id does not resolve (404)", status == 404, str(status))


def phase_idempotency(ctx: Ctx) -> None:
    ev, ob = ctx.medium_evidence, ctx.medium_object
    inv = ctx.clients["investigator"]
    before = len(inv.json("GET", ctx.runs())[1]["items"])
    first = ctx.start("investigator", ev, ob, "idem-key-0000001")
    again = ctx.start("investigator", ev, ob, "idem-key-0000001")
    ctx.check(
        "the same key and request returns the ORIGINAL run (201 then 200)",
        (first[0], again[0]) == (201, 200) and first[1]["id"] == again[1]["id"],
        f"{first[0]},{again[0]}",
    )
    other_ev, other_ob = ctx.upload_bytes(base.PDF_PREFIX + b"another object")
    conflict = ctx.start("investigator", other_ev, other_ob, "idem-key-0000001")
    ctx.check(
        "the same key with a different request is 409 idempotency_conflict",
        conflict[0] == 409 and conflict[1]["error"]["code"] == "idempotency_conflict",
    )
    results: list[tuple[int, str]] = []

    def attempt() -> None:
        status, body = ctx.start("investigator", ev, ob, "idem-concurrent-001")
        results.append((status, str(body.get("id"))))

    threads = [threading.Thread(target=attempt) for _ in range(10)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    ids = {run_id for _, run_id in results}
    ctx.check(
        "ten concurrent identical requests create exactly one run",
        len(ids) == 1
        and [s for s, _ in results].count(201) == 1
        and {s for s, _ in results} <= {200, 201},
        f"{sorted(s for s, _ in results)}",
    )
    ctx.wait_for(first[1]["id"], {"completed", "failed"})
    ctx.wait_for(next(iter(ids)), {"completed", "failed"})
    after = len(inv.json("GET", ctx.runs())[1]["items"])
    ctx.check(
        "those requests added exactly two runs in total",
        after == before + 2,
        f"{before} -> {after}",
    )


def phase_large_object(ctx: Ctx) -> None:
    inv = ctx.clients["investigator"]
    body = HistogramBody(LARGE_BYTES)
    started = time.monotonic()
    ctx.large_evidence, ctx.large_object = ctx.upload_object(body, LARGE_BYTES)
    ctx.large_hist = body.histogram
    ctx.large_target = ctx.storage_path(ctx.large_object)
    ctx.check(
        "a 96 MiB object was preserved through nginx",
        True,
        f"{time.monotonic() - started:.0f}s",
    )

    grew = base.control_memory_probe(ctx.stack)
    ctx.check(
        "instrument control: a known 64 MiB allocation is visible to the memory probe",
        grew >= 60 * MIB,
        f"{grew // MIB} MiB",
    )

    sampler = base.Sampler(ctx.stack)
    anon_before = base.backend_anon_bytes(ctx.stack)
    sampler.start()
    status, run = ctx.start("investigator", ctx.large_evidence, ctx.large_object)
    ctx.check("the 96 MiB run is queued", status == 201, str(status))
    beats: list[str] = []
    seen: list[str] = []
    deadline = time.monotonic() + 240
    final: dict[str, Any] = {}
    while time.monotonic() < deadline:
        current = ctx.detail(run["id"])
        if not seen or seen[-1] != current["state"]:
            seen.append(current["state"])
        if current["state"] == "running" and current["last_heartbeat_at"] not in beats:
            beats.append(current["last_heartbeat_at"])
        if current["state"] in {"completed", "failed", "cancelled"}:
            final = current
            break
        time.sleep(0.1)
    sampler.halt.set()
    sampler.join()
    ctx.check(
        "RUNNING was observed over HTTP for the large object",
        "running" in seen,
        " -> ".join(seen),
    )
    ctx.check(
        "the heartbeat advanced while it ran",
        len(beats) >= 2,
        f"{len(beats)} distinct heartbeats",
    )
    ctx.check("the 96 MiB run COMPLETED", final.get("state") == "completed", " -> ".join(seen))
    statements = [o["statement"] for o in final.get("observations", [])]
    ctx.check(
        "its Observations equal the independent computation over 96 MiB",
        statements_match(statements, expected_statements(ctx.large_hist)),
        statements[0] if statements else "none",
    )
    peak = max(sampler.anon or [anon_before])
    ctx.check(
        "backend heap memory stayed bounded while examining 96 MiB",
        peak - anon_before < MEMORY_GROWTH_LIMIT,
        f"growth {(peak - anon_before) / MIB:.1f} MiB (limit {MEMORY_GROWTH_LIMIT // MIB} MiB)",
    )
    ctx.check(
        "the object was not copied: the evidence volume holds the same single file",
        ctx.sha256_in_container(ctx.large_target) == body.sha256.hexdigest(),
    )
    del inv


def phase_cancellation(ctx: Ctx) -> None:
    ev, ob = ctx.large_evidence, ctx.large_object
    inv = ctx.clients["investigator"]
    status, running = ctx.start("investigator", ev, ob)
    ctx.wait_for(running["id"], {"running"})
    status_q, queued = ctx.start("investigator", ev, ob)
    ctx.check(
        "a second run waits QUEUED behind the running one",
        status_q == 201 and queued["state"] == "queued",
        str(status_q),
    )
    status, cancelled = inv.json("POST", ctx.runs(f"/{queued['id']}/cancel"))
    ctx.check(
        "cancelling a QUEUED run is immediate (200 cancelled)",
        status == 200 and cancelled["state"] == "cancelled",
        str(status),
    )
    ctx.check(
        "a cancelled queued run is never claimed",
        ctx.actions_of(queued["id"])[-1][0] == "examination.run.cancelled"
        and all(a != "examination.run.started" for a, _ in ctx.actions_of(queued["id"])),
    )
    status, requested = inv.json("POST", ctx.runs(f"/{running['id']}/cancel"))
    ctx.check(
        "cancelling a RUNNING run only requests it (202, still running)",
        status == 202 and requested["cancel_requested_at"] is not None,
        f"{status} {requested.get('state')}",
    )
    done, _ = ctx.wait_for(running["id"], {"cancelled", "completed", "failed"})
    ctx.check(
        "the worker stopped it at a checkpoint: CANCELLED, never COMPLETED",
        done["state"] == "cancelled",
        done["state"],
    )
    ctx.check("a cancelled run published no Observations", done["observations"] == [])
    ctx.check(
        "audit: created, started, cancel_requested, cancelled",
        [a for a, _ in ctx.actions_of(running["id"])]
        == [
            "examination.run.created",
            "examination.run.started",
            "examination.run.cancel_requested",
            "examination.run.cancelled",
        ],
        str(ctx.actions_of(running["id"])),
    )
    status, body = inv.json("POST", ctx.runs(f"/{running['id']}/cancel"))
    ctx.check(
        "a finished run cannot be cancelled again (409)",
        status == 409 and body["error"]["code"] == "invalid_lifecycle_transition",
        str(status),
    )


def phase_failure_and_retry(ctx: Ctx) -> None:
    inv = ctx.clients["investigator"]
    ev, ob, target = ctx.medium_evidence, ctx.medium_object, ctx.target

    # Tampered bytes (same object, one appended byte): the run fails closed.
    ctx.stack.exec("sh", "-c", f"printf X >> {target}", user="veritas")
    _, failed = ctx.start("investigator", ev, ob)
    failed, _ = ctx.wait_for(failed["id"], {"completed", "failed"})
    ctx.check(
        "tampered bytes: the run FAILED, it did not complete",
        failed["state"] == "failed",
        failed["state"],
    )
    ctx.check(
        "tampered bytes: failure_code is integrity_mismatch",
        failed["failure_code"] == "integrity_mismatch",
    )
    ctx.check("tampered bytes: no Observation was published", failed["observations"] == [])
    ctx.check(
        "tampered bytes: the failure message is the fixed, sanitized sentence",
        "integrity comparison only" in (failed["failure_message"] or "")
        and "/" not in (failed["failure_message"] or ""),
    )
    ctx.check(
        "tampered bytes: audit has one failed event with the code",
        [a for a, _ in ctx.actions_of(failed["id"])]
        == [
            "examination.run.created",
            "examination.run.started",
            "examination.run.failed",
        ],
    )
    _, verification = inv.json("POST", f"{ctx.object_base(ev, ob)}/verify")
    ctx.check(
        "V2.2 verification independently reports MISMATCH for the same bytes",
        verification.get("result") == "MISMATCH",
    )
    ctx.check(
        "the failed run is retained unchanged as history",
        ctx.detail(failed["id"])["state"] == "failed",
    )

    # Restore the bytes, then retry: a NEW run.
    ctx.restore()
    old_snapshot = json.dumps(ctx.detail(failed["id"]), sort_keys=True)
    status, retried = ctx.retry(failed["id"], "retry-key-0000001")
    ctx.check(
        "retry creates a NEW run (201)",
        status == 201 and retried["id"] != failed["id"],
        str(status),
    )
    ctx.check(
        "the retry copies the intent",
        all(
            retried[k] == failed[k]
            for k in (
                "evidence_id",
                "evidence_object_id",
                "method_key",
                "method_version",
                "parameters",
            )
        ),
    )
    again, _ = ctx.wait_for(retried["id"], {"completed", "failed"})
    ctx.check("the retried run COMPLETED", again["state"] == "completed", again["state"])
    ctx.check(
        "the old failed run is byte-for-byte unchanged",
        json.dumps(ctx.detail(failed["id"]), sort_keys=True) == old_snapshot,
    )
    retry_trail = ctx.actions_of(retried["id"])
    ctx.check(
        "audit: the new run starts with examination.run.retried",
        retry_trail[0][0] == "examination.run.retried",
        str(retry_trail[:1]),
    )
    status, replay = ctx.retry(failed["id"], "retry-key-0000001")
    ctx.check(
        "retry is idempotent (200, same run)",
        status == 200 and replay["id"] == retried["id"],
        str(status),
    )

    # Missing and unreadable objects are refused at creation (503), never reported as a result.
    runs_before = ctx.run_count()
    ctx.stack.exec("sh", "-c", f"mv {target} {target}.away", user="veritas")
    status, body = ctx.start("investigator", ev, ob)
    ctx.check(
        "a missing object is 503 evidence_storage_unavailable at creation",
        status == 503 and body["error"]["code"] == "evidence_storage_unavailable",
        str(status),
    )
    ctx.stack.exec("sh", "-c", f"mv {target}.away {target} && chmod 000 {target}", user="veritas")
    status, body = ctx.start("investigator", ev, ob)
    ctx.check(
        "an unreadable object (mode 000) is 503 at creation",
        status == 503 and body["error"]["code"] == "evidence_storage_unavailable",
        str(status),
    )
    ctx.stack.exec("sh", "-c", f"chmod 600 {target}", user="veritas")
    ctx.check(
        "neither case created a run, and no error leaked a path",
        ctx.run_count() == runs_before and "/var/lib/" not in json.dumps(body),
    )

    # Truncation while a run is executing (a real storage fault on a spare 64 MiB object).
    body_spare = HistogramBody(MEDIUM_BYTES)
    ctx.spare_evidence, ctx.spare_object = ctx.upload_object(body_spare, MEDIUM_BYTES)
    ctx.spare_hist = body_spare.histogram
    ctx.spare_target = ctx.storage_path(ctx.spare_object)
    _, run = ctx.start("investigator", ctx.spare_evidence, ctx.spare_object)
    ctx.wait_for(run["id"], {"running"})
    ctx.stack.exec("sh", "-c", f"truncate -s {MIB} {ctx.spare_target}", user="veritas")
    result, _ = ctx.wait_for(run["id"], {"completed", "failed", "cancelled"})
    ctx.check(
        "truncation during execution: the run FAILED closed, no partial Observations",
        result["state"] == "failed"
        and result["observations"] == []
        and result["failure_code"] == "integrity_mismatch",
        f"{result['state']} {result['failure_code']}",
    )


def phase_database_outage(ctx: Ctx) -> None:
    inv = ctx.clients["investigator"]
    ev, ob = ctx.large_evidence, ctx.large_object
    _, run = ctx.start("investigator", ev, ob)
    ctx.wait_for(run["id"], {"running"})
    base.docker("stop", ctx.stack.container("db"))
    time.sleep(15)  # longer than the run and than the stale threshold
    status, body = base.Client().json("GET", "/ready")
    ctx.check("with the database stopped /ready is 503", status == 503, str(status))
    status, body = inv.json("GET", ctx.runs())
    ctx.check(
        "with the database stopped the API fails with a sanitized error",
        status in (500, 503)
        and "Traceback" not in json.dumps(body)
        and "postgres" not in json.dumps(body).lower(),
        str(status),
    )
    base.docker("start", ctx.stack.container("db"))
    ctx.check(
        "the database came back and /ready recovered without restarting the backend",
        base.wait_ready(120),
    )
    final, _ = ctx.wait_for(run["id"], {"completed", "failed", "cancelled"}, timeout=240)
    ctx.check(
        "the interrupted run was recovered and COMPLETED",
        final["state"] == "completed",
        final["state"],
    )
    ctx.check(
        "it published exactly one set of five Observations",
        len(final["observations"]) == 5,
    )
    trail = [a for a, _ in ctx.actions_of(run["id"])]
    ctx.check(
        "audit shows the interruption: a recovery, then exactly one completion",
        trail.count("examination.run.completed") == 1
        and ("examination.run.recovered" in trail or trail.count("examination.run.started") == 1),
        str(trail),
    )
    ctx.clients["investigator"].login(f"investigator-{ctx.suffix}")


def phase_restart_recovery(ctx: Ctx) -> None:
    inv = ctx.clients["investigator"]
    ev, ob = ctx.large_evidence, ctx.large_object

    # Graceful restart (SIGTERM) with one run executing and one queued behind it.
    _, first = ctx.start("investigator", ev, ob)
    ctx.wait_for(first["id"], {"running"})
    _, second = ctx.start("investigator", ev, ob)
    ctx.stack.run("restart", "backend")
    ctx.check("backend ready again after a graceful restart", base.wait_ready())
    inv.login(f"investigator-{ctx.suffix}")
    done_first, _ = ctx.wait_for(first["id"], {"completed", "failed", "cancelled"}, timeout=240)
    done_second, _ = ctx.wait_for(second["id"], {"completed", "failed", "cancelled"}, timeout=240)
    ctx.check(
        "graceful restart: running and queued runs both COMPLETED, five Observations each",
        done_first["state"] == done_second["state"] == "completed"
        and len(done_first["observations"]) == len(done_second["observations"]) == 5,
        f"{done_first['state']}/{done_second['state']}",
    )
    first_trail = [a for a, _ in ctx.actions_of(first["id"])]
    ctx.check(
        "graceful restart: the interrupted run was released and re-run (one completion)",
        first_trail.count("examination.run.completed") == 1,
        str(first_trail),
    )

    # Hard crash (SIGKILL) while a run is executing.
    _, victim = ctx.start("investigator", ev, ob)
    ctx.wait_for(victim["id"], {"running"})
    base.docker("kill", ctx.stack.container("backend"))
    ctx.stack.run("up", "-d", "--wait")
    ctx.check("backend ready again after SIGKILL", base.wait_ready())
    inv.login(f"investigator-{ctx.suffix}")
    mid = ctx.detail(victim["id"])
    ctx.check(
        "right after the crash the run is not lost (queued or still marked running)",
        mid["state"] in {"queued", "running", "completed"},
        mid["state"],
    )
    recovered, _ = ctx.wait_for(victim["id"], {"completed", "failed", "cancelled"}, timeout=240)
    ctx.check(
        "after SIGKILL the run was recovered and COMPLETED",
        recovered["state"] == "completed",
        recovered["state"],
    )
    ctx.check(
        "it published exactly one set of five Observations",
        len(recovered["observations"]) == 5,
    )
    trail = [a for a, _ in ctx.actions_of(victim["id"])]
    ctx.check(
        "audit: the crash is visible as a recovery before the single completion",
        "examination.run.recovered" in trail and trail.count("examination.run.completed") == 1,
        str(trail),
    )

    # Container recreation (volumes kept): history and objects persist.
    ctx.stack.run("down")
    ctx.stack.run("up", "-d", "--wait")
    ctx.check("after the containers were recreated the stack is ready", base.wait_ready())
    inv.login(f"investigator-{ctx.suffix}")
    ctx.check(
        "after recreation the completed run and its Observations persist",
        len(ctx.detail(victim["id"])["observations"]) == 5,
    )
    status, verified = inv.json("POST", f"{ctx.object_base(ev, ob)}/verify")
    ctx.check(
        "after recreation the large object still verifies MATCH",
        status == 200 and verified.get("result") == "MATCH",
    )


def phase_no_leaks(ctx: Ctx) -> None:
    inv = ctx.clients["investigator"]
    names = (
        ctx.stack.exec("sh", "-c", f"ls -1 {base.EVIDENCE_DIR}/preserved").stdout.decode().split()
    )
    keys = {n.removesuffix(".bin") for n in names if n.endswith(".bin")}
    _, audit = inv.json("GET", f"/api/v1/cases/{ctx.case}/audit-events?limit=500")
    _, runs = inv.json("GET", ctx.runs())
    blob = json.dumps([audit, runs])
    ctx.check(
        "no storage key appears in any audit event or run",
        not any(k in blob for k in keys),
    )
    ctx.check(
        "no filesystem path appears in any audit event or run",
        base.EVIDENCE_DIR not in blob and "/var/lib" not in blob,
    )
    ctx.check(
        "no idempotency key or fingerprint is exposed",
        not any(k in blob for k in ctx.used_keys)
        and "request_fingerprint" not in blob
        and "idempotency" not in blob,
        f"{len(ctx.used_keys)} keys checked",
    )
    logs = base.docker("logs", ctx.stack.container("backend"), check=False)
    ctx.check(
        "no storage key, path or traceback in the backend logs",
        not any(k in logs for k in keys)
        and base.EVIDENCE_DIR not in logs
        and "Traceback" not in logs,
    )
    ctx.check(
        "the worker logged only identifiers and exception types",
        "examination_worker_started" in logs,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--project", default="veritas-v23-verify")
    parser.add_argument("--keep", action="store_true", help="leave the stack running afterwards")
    args = parser.parse_args()
    if (
        not PROJECT_PATTERN.fullmatch(args.project)
        or f"{args.project}_pgdata" in base.PROTECTED_VOLUMES
    ):
        print(
            f"refusing project {args.project!r}: it must match {PROJECT_PATTERN.pattern}",
            file=sys.stderr,
        )
        return 2
    report = base.Report()
    workdir = Path(tempfile.mkdtemp(prefix="veritas-v23-runtime-"))
    stack = Stack23(args.project, workdir)

    def protected_volumes() -> set[str]:
        return {
            v
            for v in base.docker("volume", "ls", "--format", "{{.Name}}").split()
            if v in base.PROTECTED_VOLUMES
        }

    protected_before = protected_volumes()
    ctx = Ctx(stack, report)
    try:
        phase_bring_up(ctx)
        phase_provision(ctx)
        phase_end_to_end(ctx)
        phase_authorization(ctx)
        phase_idempotency(ctx)
        phase_large_object(ctx)
        phase_cancellation(ctx)
        phase_failure_and_retry(ctx)
        phase_database_outage(ctx)
        phase_restart_recovery(ctx)
        phase_no_leaks(ctx)
    except Exception as exc:  # report it, then always tear down
        report.check(
            "runtime check completed without an unexpected error",
            False,
            f"{type(exc).__name__}: {exc}",
        )
    finally:
        if not args.keep:
            stack.run("down", "-v", "--remove-orphans", check=False)
            volumes = base.docker("volume", "ls", "--format", "{{.Name}}").split()
            leftover = [v for v in volumes if v.startswith(args.project)]
            report.check(
                "teardown removed this project's containers and volumes",
                not leftover,
                ", ".join(leftover),
            )
        report.check(
            "protected volumes were not created, removed or modified",
            protected_volumes() == protected_before,
            f"present on this host before and after: {sorted(protected_before) or 'none'}",
        )
        shutil.rmtree(workdir, ignore_errors=True)
    failed = [name for name, passed, _ in report.rows if not passed]
    print(f"\n{len(report.rows) - len(failed)} passed, {len(failed)} failed")
    for name in failed:
        print(f"  FAILED: {name}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
