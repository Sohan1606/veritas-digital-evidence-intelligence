"""V2.2 runtime verification against a DISPOSABLE Docker Compose project (not part of check.sh).

Builds and starts the shipped stack under its own project name, then exercises retrieval and
integrity verification over real HTTP through nginx: authorization, byte-exact retrieval,
verification, large-object streaming (backend memory and nginx temp files observed), tamper
detection, missing and unreadable objects, a database outage, and restart persistence.

Safety: the project name must match ``veritas-v22-*``. The script refuses any other name, never
names the volumes of another project, removes only this project's containers and volumes, and
checks that ``veritas_pgdata`` / ``veritas-v2_pgdata`` are untouched. Nothing is written to the
repository; the generated database password and the override file live in a temporary directory.

    python3 scripts/qa/v2_2_runtime_check.py            # build, run, verify, tear down
    python3 scripts/qa/v2_2_runtime_check.py --keep     # leave the stack running afterwards

Two instruments back the streaming claims, and each is checked against a known positive before
it is trusted: backend heap memory (cgroup ``anon``, which excludes the page cache) and nginx
spooling. nginx unlinks its proxy temp files as soon as it creates them, so a directory listing
cannot see them; the probe instead sums the size of open-but-deleted files under
``/tmp/proxy_temp`` through ``/proc/*/fd``.

Requires Docker with the Compose plugin and permission to use it. Exit status is non-zero if any
check fails. Port 8080 on 127.0.0.1 must be free.
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
PROJECT_PATTERN = re.compile(r"^veritas-v22-[a-z0-9-]{1,30}$")
PROTECTED_VOLUMES = frozenset({"veritas_pgdata", "veritas-v2_pgdata"})
HOST, PORT = "127.0.0.1", 8080
PASSWORD = "Synthetic-Runtime-Check-2026!"  # noqa: S105 - disposable synthetic accounts only
MIB = 1024 * 1024
PDF_PREFIX = b"%PDF-1.7\n"
EVIDENCE_DIR = "/var/lib/veritas/evidence"
LARGE_BYTES = 96 * MIB
# A buffering implementation grows by about the object size (96 MiB); streaming stays far below.
MEMORY_GROWTH_LIMIT = 40 * MIB
MATCH_MESSAGE = "Preserved bytes match the recorded intake integrity values."


@dataclass
class Report:
    rows: list[tuple[str, bool, str]] = field(default_factory=list)

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        self.rows.append((name, ok, detail))
        suffix = f"  [{detail}]" if detail else ""
        print(f"{'PASS' if ok else 'FAIL'}  {name}{suffix}", flush=True)
        return ok


class Stack:
    """The disposable Compose project."""

    def __init__(self, project: str, workdir: Path) -> None:
        self.project = project
        self.override = workdir / "override.yml"
        self.envfile = workdir / ".env"
        self.override.write_text(
            "services:\n  backend:\n    environment:\n      VERITAS_ACCESS_MODE: restricted\n"
        )
        self.envfile.write_text(f"POSTGRES_PASSWORD={secrets.token_urlsafe(24)}\n")
        self.envfile.chmod(0o600)

    def compose(self) -> list[str]:
        compose_file = str(ROOT / "docker-compose.yml")
        return [
            *("docker", "compose", "-p", self.project),
            *("-f", compose_file, "-f", str(self.override), "--env-file", str(self.envfile)),
        ]

    def run(
        self, *args: str, stdin: bytes | None = None, check: bool = True
    ) -> subprocess.CompletedProcess[bytes]:
        result = subprocess.run(  # noqa: S603 - fixed argument list, no shell
            [*self.compose(), *args], input=stdin, capture_output=True, cwd=ROOT, check=False
        )
        if check and result.returncode != 0:
            tail = result.stderr.decode(errors="replace")[-400:]
            raise RuntimeError(f"docker compose {' '.join(args)} failed: {tail}")
        return result

    def exec(
        self,
        *command: str,
        user: str | None = None,
        stdin: bytes | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[bytes]:
        flags = ["-T", *(["-u", user] if user else [])]
        return self.run("exec", *flags, "backend", *command, stdin=stdin, check=check)

    def container(self, service: str) -> str:
        return f"{self.project}-{service}-1"


def docker(*args: str, check: bool = True) -> str:
    result = subprocess.run(["docker", *args], capture_output=True, check=False)  # noqa: S603, S607
    if check and result.returncode != 0:
        raise RuntimeError(f"docker {' '.join(args)} failed: {result.stderr.decode()[-300:]}")
    return result.stdout.decode(errors="replace")


# --- HTTP through nginx ---------------------------------------------------------------------


class Client:
    """A minimal cookie-aware HTTP client for http://127.0.0.1:8080 (the nginx front door)."""

    def __init__(self) -> None:
        self.cookies: dict[str, str] = {}

    @property
    def csrf(self) -> str:
        return self.cookies.get("veritas_csrf", "")

    def request(
        self,
        method: str,
        path: str,
        *,
        body: Any = None,
        headers: dict[str, str] | None = None,
        csrf: bool = True,
        read: bool = True,
        timeout: float = 120,
    ) -> tuple[http.client.HTTPResponse, bytes, http.client.HTTPConnection]:
        """Send one request. With ``read=False`` the caller streams the body and closes ``conn``."""
        conn = http.client.HTTPConnection(HOST, PORT, timeout=timeout)
        sent = {"Cookie": "; ".join(f"{k}={v}" for k, v in self.cookies.items())}
        sent.update(headers or {})
        if csrf and method not in {"GET", "HEAD"} and self.csrf:
            sent["X-CSRF-Token"] = self.csrf
        if isinstance(body, dict):
            body = json.dumps(body).encode()
            sent["Content-Type"] = "application/json"
        conn.request(method, path, body=body, headers={k: v for k, v in sent.items() if v})
        response = conn.getresponse()
        for header, value in response.getheaders():
            if header.lower() == "set-cookie":
                name, _, rest = value.partition("=")
                self.cookies[name] = rest.split(";", 1)[0]
        data = b""
        if read:
            data = response.read()
            conn.close()
        return response, data, conn

    def json(self, method: str, path: str, **kwargs: Any) -> tuple[int, dict[str, Any]]:
        response, data, _ = self.request(method, path, **kwargs)
        try:
            parsed = json.loads(data or b"{}")
        except ValueError:
            parsed = {}
        return response.status, parsed if isinstance(parsed, dict) else {}

    def login(self, username: str) -> None:
        payload = {"username": username, "password": PASSWORD}
        status, body = self.json("POST", "/api/v1/auth/login", body=payload)
        if status != 200:
            raise RuntimeError(f"login failed for {username}: {status} {body}")


class PatternBody:
    """A file-like request body of ``size`` incompressible bytes (PDF signature first)."""

    def __init__(self, size: int) -> None:
        self.size, self.sent = size, 0
        self.sha256 = hashlib.sha256()
        self._seed = secrets.token_bytes(32)

    def read(self, amount: int = -1) -> bytes:
        amount = min(self.size - self.sent, 256 * 1024 if amount < 0 else amount)
        if amount <= 0:
            return b""
        block = hashlib.shake_256(self._seed + self.sent.to_bytes(8, "big")).digest(amount)
        if self.sent == 0:
            block = PDF_PREFIX + block[len(PDF_PREFIX) :]
        self.sent += amount
        self.sha256.update(block)
        return block


# --- Observation ----------------------------------------------------------------------------


# Sum of the sizes of files under /tmp/proxy_temp that are open but already unlinked (nginx
# deletes a proxy temp file right after creating it, so `du` on the directory reports 0).
SPOOL_PROBE = (
    'total=0; for f in /proc/[0-9]*/fd/*; do t=$(readlink "$f" 2>/dev/null); '
    'case "$t" in /tmp/proxy_temp/*) s=$(stat -Lc %s "$f" 2>/dev/null || echo 0); '
    "total=$((total+s));; esac; done; echo $total"
)


def web_spooled_bytes(stack: Stack) -> int:
    out = docker("exec", stack.container("web"), "sh", "-c", SPOOL_PROBE, check=False)
    return int(out.strip()) if out.strip().isdigit() else 0


def control_spool_probe(stack: Stack) -> int:
    """Hold a known 2 MiB unlinked file under /tmp/proxy_temp and report what the probe sees."""
    snippet = (
        "f=/tmp/proxy_temp/instrument-control; head -c 2097152 /dev/zero > $f; "
        f"exec 9<$f; rm -f $f; {SPOOL_PROBE}; exec 9<&-"
    )
    out = docker("exec", stack.container("web"), "sh", "-c", snippet, check=False)
    return int(out.strip().splitlines()[-1]) if out.strip() else 0


def control_memory_probe(stack: Stack) -> int:
    """Allocate a known 64 MiB in the backend container and report the cgroup ``anon`` growth."""
    code = (
        "def anon():\n"
        "    lines = open('/sys/fs/cgroup/memory.stat').read().splitlines()\n"
        "    return int([x for x in lines if x.startswith('anon ')][0].split()[1])\n"
        "before = anon()\n"
        "held = b'x' * (64 * 1024 * 1024)\n"
        "print(anon() - before + 0 * len(held))\n"
    )
    return int(stack.exec("python", "-c", code).stdout.decode().strip().splitlines()[-1])


def backend_anon_bytes(stack: Stack) -> int:
    """Anonymous (heap) memory of the backend container's cgroup; excludes the page cache."""
    command = "grep '^anon ' /sys/fs/cgroup/memory.stat"
    return int(docker("exec", stack.container("backend"), "sh", "-c", command).split()[1])


class Sampler(threading.Thread):
    """Samples backend heap memory and nginx spooling while a transfer runs."""

    def __init__(self, stack: Stack) -> None:
        super().__init__(daemon=True)
        self.stack = stack
        self.halt = threading.Event()
        self.anon: list[int] = []
        self.spooled: list[int] = []

    def run(self) -> None:
        while not self.halt.is_set():
            try:
                self.anon.append(backend_anon_bytes(self.stack))
                self.spooled.append(web_spooled_bytes(self.stack))
            except (RuntimeError, ValueError, IndexError):
                pass
            time.sleep(0.1)


def wait_ready(timeout: float = 150) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            conn = http.client.HTTPConnection(HOST, PORT, timeout=5)
            conn.request("GET", "/ready")
            if conn.getresponse().status == 200:
                return True
        except OSError:
            pass
        time.sleep(1)
    return False


def preserved_files(stack: Stack) -> set[str]:
    listing = f"ls -1 {EVIDENCE_DIR}/preserved 2>/dev/null || true"
    out = stack.exec("sh", "-c", listing).stdout.decode()
    return {line for line in out.split() if line.endswith(".bin")}


# --- Shared state ----------------------------------------------------------------------------


@dataclass
class Ctx:
    stack: Stack
    report: Report
    suffix: str = field(default_factory=lambda: secrets.token_hex(3))
    case: str = ""
    clients: dict[str, Client] = field(default_factory=dict)
    small: bytes = b""
    base: str = ""
    object_id: str = ""
    target: str = ""
    large_base: str = ""
    large_sha256: str = ""
    events_before_restart: int = 0

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        return self.report.check(name, ok, detail)

    def preserve(self, body: Any, size: int, *, finalize: bool = True) -> tuple[str, str]:
        """Register, upload and (by default) finalize one object through nginx."""
        investigator, custodian = self.clients["investigator"], self.clients["custodian"]
        intake = {
            "label": "Synthetic runtime object",
            "evidence_type": "document",
            "original_filename": "runtime-check.pdf",
            "declared_media_type": "application/pdf",
        }
        status, detail = investigator.json(
            "POST", f"/api/v1/cases/{self.case}/evidence/intake", body=intake
        )
        if status != 201:
            raise RuntimeError(f"intake failed: {status} {detail}")
        evidence_id = str(detail["evidence"]["id"])
        object_id = str(detail["objects"][0]["id"])
        base = f"/api/v1/cases/{self.case}/evidence/{evidence_id}/objects/{object_id}"
        headers = {"Content-Type": "application/octet-stream", "Content-Length": str(size)}
        status, uploaded = investigator.json(
            "PUT", f"{base}/content", body=body, headers=headers, timeout=300
        )
        if status != 200:
            raise RuntimeError(f"upload failed: {status} {uploaded}")
        if finalize:
            status, final = custodian.json("POST", f"{base}/finalize")
            if status != 200 or final.get("state") != "PRESERVED":
                raise RuntimeError(f"finalize failed: {status} {final}")
        return evidence_id, object_id

    def verify_result(self) -> str:
        _, body = self.clients["investigator"].json("POST", f"{self.base}/verify")
        return str(body.get("result"))

    def retrieve(self, client: str = "investigator") -> tuple[int, bytes]:
        response, data, _ = self.clients[client].request("GET", f"{self.base}/content")
        return response.status, data

    def restore(self) -> None:
        command = f"rm -rf {self.target} && cat > {self.target}"
        self.stack.exec("sh", "-c", command, user="veritas", stdin=self.small)


# --- Phases ----------------------------------------------------------------------------------


def phase_bring_up(ctx: Ctx) -> None:
    stack = ctx.stack
    config = json.loads(stack.run("config", "--format", "json").stdout)
    services = config["services"]
    backend_mounts = [v["target"] for v in services["backend"].get("volumes", [])]
    ctx.check(
        "project is disposable and named veritas-v22-*",
        PROJECT_PATTERN.fullmatch(config["name"]) is not None,
        config["name"],
    )
    ctx.check(
        "compose: only backend mounts the evidence volume",
        backend_mounts == [EVIDENCE_DIR] and not services["web"].get("volumes"),
    )
    published = [name for name, service in services.items() if service.get("ports")]
    ctx.check("compose: only nginx publishes a port", published == ["web"], str(published))
    stack.run("down", "-v", "--remove-orphans", check=False)
    stack.run("up", "-d", "--build", "--wait")
    health = {
        name: docker("inspect", "-f", "{{.State.Health.Status}}", stack.container(name)).strip()
        for name in ("db", "backend", "web")
    }
    ctx.check("db, backend and nginx are healthy", set(health.values()) == {"healthy"}, str(health))
    ctx.check("GET /ready through nginx (database ok, schema ok)", wait_ready())
    web_mounts = json.loads(docker("inspect", "-f", "{{json .Mounts}}", stack.container("web")))
    probe = docker(
        "exec", stack.container("web"), "sh", "-c", "ls -d /var/lib/veritas 2>&1 || true"
    )
    ctx.check(
        "running nginx container has no mounts and no evidence path",
        web_mounts == [] and "No such file" in probe,
    )
    backend_mounts_live = json.loads(
        docker("inspect", "-f", "{{json .Mounts}}", stack.container("backend"))
    )
    ctx.check(
        "running backend has the evidence volume",
        any(m["Destination"] == EVIDENCE_DIR for m in backend_mounts_live),
    )


def phase_provision(ctx: Ctx) -> None:
    script = (
        "from app.core.config import get_settings\n"
        "from app.db.session import build_engine, build_session_factory\n"
        "from app.services.records import create_case\n"
        "factory = build_session_factory(build_engine(get_settings()))\n"
        "with factory() as s, s.begin():\n"
        "    case = create_case(s, actor='system:runtime-check', title='Synthetic runtime Case',\n"
        "                       summary=None, is_demonstration=False)\n"
        "    print(case.public_id)\n"
    )
    ctx.case = ctx.stack.exec("python", "-c", script).stdout.decode().strip().splitlines()[-1]
    roles = {
        "investigator": ("INVESTIGATOR", ctx.case),
        "custodian": ("CUSTODIAN", ctx.case),
        "auditor": ("AUDITOR", None),
        "administrator": ("ADMINISTRATOR", None),
    }
    for name, (role, scope) in roles.items():
        command = ["python", "-m", "app.provision", "--username", f"{name}-{ctx.suffix}"]
        command += ["--display-name", f"Runtime {name}", "--role", role]
        if scope:
            command += ["--case-id", scope]
        result = ctx.stack.exec(*command, stdin=f"{PASSWORD}\n{PASSWORD}\n".encode(), check=False)
        ctx.check(f"provisioned {role}", result.returncode == 0)
        ctx.clients[name] = Client()
        ctx.clients[name].login(f"{name}-{ctx.suffix}")
    ctx.clients["anonymous"] = Client()


def phase_authorization_and_exact_retrieval(ctx: Ctx) -> None:
    stack, clients = ctx.stack, ctx.clients
    ctx.small = PDF_PREFIX + b"Synthetic runtime bytes " + secrets.token_bytes(200_000)
    before = preserved_files(stack)
    evidence_id, ctx.object_id = ctx.preserve(ctx.small, len(ctx.small))
    ctx.base = f"/api/v1/cases/{ctx.case}/evidence/{evidence_id}/objects/{ctx.object_id}"
    created = preserved_files(stack) - before
    ctx.check("object was preserved in backend-only storage", len(created) == 1)
    ctx.target = f"{EVIDENCE_DIR}/preserved/{next(iter(created))}" if created else ""

    response, data, _ = clients["investigator"].request("GET", f"{ctx.base}/content")
    headers = {k.lower(): v for k, v in response.getheaders()}
    ctx.check(
        "retrieval is byte-exact (investigator)", response.status == 200 and data == ctx.small
    )
    safe_headers = (
        headers.get("content-disposition") == f'attachment; filename="{ctx.object_id}.bin"'
        and headers.get("content-length") == str(len(ctx.small))
        and headers.get("content-type") == "application/pdf"
        and headers.get("cache-control") == "no-store"
        and "runtime-check.pdf" not in str(headers)
    )
    ctx.check("retrieval headers are safe and deterministic", safe_headers)
    nosniff = [v for k, v in response.getheaders() if k.lower() == "x-content-type-options"]
    ctx.check("nosniff appears exactly once through nginx", nosniff == ["nosniff"], str(nosniff))
    server = headers.get("server", "")
    ctx.check(
        "nginx version is not disclosed", bool(server) and not re.search(r"\d", server), server
    )

    status, data = ctx.retrieve("auditor")
    ctx.check(
        "retrieval is byte-exact (organization-scoped auditor)", status == 200 and data == ctx.small
    )
    ctx.check("administrator is denied retrieval (404)", ctx.retrieve("administrator")[0] == 404)
    ctx.check("unauthenticated retrieval is rejected (401)", ctx.retrieve("anonymous")[0] == 401)

    status, verified = clients["investigator"].json("POST", f"{ctx.base}/verify")
    documented = (
        verified.get("result") == "MATCH"
        and verified.get("message") == MATCH_MESSAGE
        and verified.get("sha256") == hashlib.sha256(ctx.small).hexdigest()
        and "expected_sha256" not in verified
    )
    ctx.check("verification MATCH with the documented fields", status == 200 and documented)
    ctx.check(
        "administrator is denied verification (404)",
        clients["administrator"].json("POST", f"{ctx.base}/verify")[0] == 404,
    )
    no_csrf = clients["investigator"].json("POST", f"{ctx.base}/verify", csrf=False)[0]
    ctx.check("verification without a CSRF token is refused (403)", no_csrf == 403)

    quarantined, q_object = ctx.preserve(ctx.small[:5000], 5000, finalize=False)
    q_base = f"/api/v1/cases/{ctx.case}/evidence/{quarantined}/objects/{q_object}"
    get_status = clients["investigator"].json("GET", f"{q_base}/content")[0]
    post_status = clients["investigator"].json("POST", f"{q_base}/verify")[0]
    ctx.check(
        "a QUARANTINED object is neither retrievable nor verifiable (409)",
        (get_status, post_status) == (409, 409),
    )


def phase_large_object(ctx: Ctx) -> None:
    investigator = ctx.clients["investigator"]
    large = PatternBody(LARGE_BYTES)
    evidence_id, object_id = ctx.preserve(large, LARGE_BYTES)
    ctx.large_base = f"/api/v1/cases/{ctx.case}/evidence/{evidence_id}/objects/{object_id}"
    ctx.large_sha256 = large.sha256.hexdigest()
    # Instrument controls: a probe must detect a known positive, or "nothing seen" means nothing.
    seen_spool = control_spool_probe(ctx.stack)
    ctx.check(
        "instrument control: the spool probe detects a known 2 MiB unlinked temp file",
        seen_spool >= 2 * MIB,
        f"saw {seen_spool // 1024} KiB",
    )
    seen_heap = control_memory_probe(ctx.stack)
    ctx.check(
        "instrument control: the memory probe detects a known 64 MiB allocation",
        seen_heap >= 60 * MIB,
        f"saw {seen_heap // MIB} MiB",
    )
    baseline = backend_anon_bytes(ctx.stack)
    sampler = Sampler(ctx.stack)
    sampler.start()
    started = time.monotonic()
    digest, total = hashlib.sha256(), 0
    response, _, conn = investigator.request(
        "GET", f"{ctx.large_base}/content", read=False, timeout=300
    )
    slow_until = time.monotonic() + 2.0  # read slowly first, when a buffering proxy would spool
    while chunk := response.read(64 * 1024):
        digest.update(chunk)
        total += len(chunk)
        if time.monotonic() < slow_until:
            time.sleep(0.02)
    conn.close()
    seconds = time.monotonic() - started
    status, verified = investigator.json("POST", f"{ctx.large_base}/verify", timeout=300)
    sampler.halt.set()
    sampler.join()
    peak = max(sampler.anon or [baseline])
    spooled = max(sampler.spooled or [0])
    label = f"{LARGE_BYTES // MIB} MiB"
    exact = total == LARGE_BYTES and digest.hexdigest() == ctx.large_sha256
    ctx.check(f"{label} object retrieved byte-exact through nginx", exact, f"{seconds:.1f}s")
    ctx.check(f"{label} object verifies MATCH", status == 200 and verified.get("result") == "MATCH")
    detail = f"baseline {baseline // MIB} MiB, peak {peak // MIB} MiB, object {label}"
    ctx.check(
        "backend memory stays bounded while streaming and hashing",
        peak - baseline < MEMORY_GROWTH_LIMIT,
        detail,
    )
    ctx.check(
        "nginx spooled nothing to disk during retrieval",
        spooled == 0,
        f"max {spooled} bytes over {len(sampler.spooled)} samples",
    )


def phase_tamper_and_storage_failures(ctx: Ctx) -> None:
    stack, investigator = ctx.stack, ctx.clients["investigator"]
    target, small = ctx.target, ctx.small

    stack.exec("sh", "-c", f"printf X >> {target}", user="veritas")
    _, mismatch = investigator.json("POST", f"{ctx.base}/verify")
    full_comparison = (
        mismatch.get("result") == "MISMATCH"
        and mismatch.get("expected_byte_size") == len(small)
        and mismatch.get("computed_byte_size") == len(small) + 1
        and mismatch.get("computed_sha256") != mismatch.get("expected_sha256")
        and "do not match" in str(mismatch.get("message"))
    )
    ctx.check("an appended byte is MISMATCH with a full comparison", full_comparison)
    status, data = ctx.retrieve()
    ctx.check(
        "retrieval stays enabled after MISMATCH and serves the stored bytes",
        status == 200 and data == small + b"X",
    )
    ctx.restore()
    ctx.check(
        "restoring the bytes verifies MATCH again (nothing sticky)", ctx.verify_result() == "MATCH"
    )

    stack.exec("sh", "-c", f"truncate -s -1 {target}", user="veritas")
    ctx.check("truncation is detected as MISMATCH", ctx.verify_result() == "MISMATCH")
    ctx.restore()
    overwrite = f"printf Z | dd of={target} bs=1 seek=300 conv=notrunc 2>/dev/null"
    stack.exec("sh", "-c", overwrite, user="veritas")
    ctx.check(
        "in-place modification (same size) is detected as MISMATCH",
        ctx.verify_result() == "MISMATCH",
    )
    ctx.restore()

    stack.exec("rm", "-f", target, user="veritas")
    status, unavailable = investigator.json("POST", f"{ctx.base}/verify")
    ctx.check(
        "a missing object is UNAVAILABLE, never MISMATCH",
        status == 200
        and unavailable.get("result") == "UNAVAILABLE"
        and "computed_sha256" not in unavailable,
    )
    status, error = investigator.json("GET", f"{ctx.base}/content")
    text = json.dumps(error)
    key = target.rsplit("/", 1)[-1]
    ctx.check(
        "retrieval of a missing object is a sanitized 503 envelope",
        status == 503
        and error.get("error", {}).get("code") == "evidence_storage_unavailable"
        and EVIDENCE_DIR not in text
        and key not in text,
    )
    ctx.restore()
    stack.exec("chmod", "000", target, user="veritas")
    unreadable = ctx.verify_result() == "UNAVAILABLE" and ctx.retrieve()[0] == 503
    ctx.check("an unreadable object is UNAVAILABLE and unretrievable (503)", unreadable)
    stack.exec("chmod", "600", target, user="veritas")
    stack.exec("sh", "-c", f"rm -f {target} && ln -s /etc/hostname {target}", user="veritas")
    ctx.check(
        "a symlink in place of the object is not followed", ctx.verify_result() == "UNAVAILABLE"
    )
    ctx.restore()
    recovered = ctx.verify_result() == "MATCH" and ctx.retrieve()[1] == small
    ctx.check("object fully recovered after the simulated storage failures", recovered)


def object_events(ctx: Ctx, client: Client) -> list[dict[str, Any]]:
    _, audit = client.json("GET", f"/api/v1/cases/{ctx.case}/audit-events?limit=500")
    return [e for e in audit.get("items", []) if e.get("entity_id") == ctx.object_id]


def phase_audit_trail(ctx: Ctx) -> None:
    events = object_events(ctx, ctx.clients["investigator"])
    retrieved = [e for e in events if e["action"] == "evidence.object.retrieved"]
    verified = [e for e in events if e["action"] == "evidence.object.integrity_verified"]
    results = {e["details"]["result"] for e in verified}
    ctx.check(
        "audit API shows retrievals and every verification result type",
        len(retrieved) >= 4 and {"MATCH", "MISMATCH", "UNAVAILABLE"} <= results,
        f"{len(retrieved)} retrieved, {len(verified)} verified, results={sorted(results)}",
    )
    blob = json.dumps(events)
    key = ctx.target.rsplit("/", 1)[-1].removesuffix(".bin")
    ctx.check(
        "audit details contain no bytes, paths or storage keys",
        EVIDENCE_DIR not in blob and "/preserved/" not in blob and key not in blob,
    )
    ctx.events_before_restart = len(events)


def phase_database_outage(ctx: Ctx) -> None:
    investigator = ctx.clients["investigator"]
    ctx.stack.run("stop", "db")
    time.sleep(2)
    probe = http.client.HTTPConnection(HOST, PORT, timeout=20)
    probe.request("GET", "/ready")
    ready = probe.getresponse().status
    status, error = investigator.json("POST", f"{ctx.base}/verify", timeout=60)
    body = json.dumps(error).lower()
    leaks = ("postgres", "password", "db:5432", "match")
    ctx.check(
        "database down: /ready is 503 and verification fails closed with a sanitized error",
        ready == 503 and status in (500, 503) and not any(word in body for word in leaks),
        f"/ready {ready}, verify {status}",
    )
    ctx.stack.run("start", "db")
    recovered = False
    for _ in range(40):
        time.sleep(1.5)
        if investigator.json("POST", f"{ctx.base}/verify", timeout=30)[1].get("result") == "MATCH":
            recovered = True
            break
    ctx.check("database back: verification works again without restarting the backend", recovered)


def phase_restart_persistence(ctx: Ctx) -> None:
    stack = ctx.stack
    stack.run("restart", "backend")
    ctx.check("backend ready again after restart", wait_ready())
    status, data = ctx.retrieve()
    ctx.check(
        "after a backend restart the same session retrieves the same bytes",
        status == 200 and data == ctx.small,
    )

    stack.run("down")  # containers and network removed, volumes kept
    stack.run("up", "-d", "--wait")
    ctx.check("after containers were recreated the stack is ready", wait_ready())
    fresh = Client()
    fresh.login(f"investigator-{ctx.suffix}")
    ctx.clients["investigator"] = fresh
    status, data = ctx.retrieve()
    ctx.check(
        "after recreation the object persisted in the evidence volume",
        status == 200 and data == ctx.small,
    )
    response, _, conn = fresh.request("GET", f"{ctx.large_base}/content", read=False, timeout=300)
    digest = hashlib.sha256()
    while chunk := response.read(256 * 1024):
        digest.update(chunk)
    conn.close()
    ctx.check(
        "after recreation the large object persisted byte-exact",
        digest.hexdigest() == ctx.large_sha256,
    )
    ctx.check("after recreation verification still MATCHes", ctx.verify_result() == "MATCH")
    after = object_events(ctx, fresh)
    ctx.check(
        "audit history survived recreation and grew",
        len(after) > ctx.events_before_restart,
        f"{ctx.events_before_restart} -> {len(after)}",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--project", default="veritas-v22-verify")
    parser.add_argument("--keep", action="store_true", help="leave the stack running afterwards")
    args = parser.parse_args()
    if not PROJECT_PATTERN.fullmatch(args.project) or f"{args.project}_pgdata" in PROTECTED_VOLUMES:
        print(
            f"refusing project {args.project!r}: it must match {PROJECT_PATTERN.pattern}",
            file=sys.stderr,
        )
        return 2
    report = Report()
    workdir = Path(tempfile.mkdtemp(prefix="veritas-v22-runtime-"))
    stack = Stack(args.project, workdir)

    def protected_volumes() -> set[str]:
        return {
            v
            for v in docker("volume", "ls", "--format", "{{.Name}}").split()
            if v in PROTECTED_VOLUMES
        }

    protected_before = protected_volumes()
    ctx = Ctx(stack, report)
    try:
        phase_bring_up(ctx)
        phase_provision(ctx)
        phase_authorization_and_exact_retrieval(ctx)
        phase_large_object(ctx)
        phase_tamper_and_storage_failures(ctx)
        phase_audit_trail(ctx)
        phase_database_outage(ctx)
        phase_restart_persistence(ctx)
    except Exception as exc:  # report it, then always tear down
        report.check(
            "runtime check completed without an unexpected error",
            False,
            f"{type(exc).__name__}: {exc}",
        )
    finally:
        if not args.keep:
            stack.run("down", "-v", "--remove-orphans", check=False)
            volumes = docker("volume", "ls", "--format", "{{.Name}}").split()
            leftover = [v for v in volumes if v.startswith(args.project)]
            report.check(
                "teardown removed this project's containers and volumes",
                not leftover,
                ", ".join(leftover),
            )
        report.check(
            "protected volumes were not created, removed or modified",
            protected_volumes() == protected_before,
        )
        shutil.rmtree(workdir, ignore_errors=True)
    failed = [name for name, passed, _ in report.rows if not passed]
    print(f"\n{len(report.rows) - len(failed)} passed, {len(failed)} failed")
    for name in failed:
        print(f"  FAILED: {name}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
