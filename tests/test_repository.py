"""Repository hygiene tests: what may and may not be committed.

Runs from the repository root with any Python >= 3.12 and pytest:  pytest tests
The file set is what git would commit (tracked + untracked, minus .gitignore), so the
checks hold before the first commit as well as in CI.
"""

from __future__ import annotations

import re
import subprocess
from functools import cache
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

MAX_FILES = 400
MAX_TOTAL_BYTES = 5 * 1024 * 1024
MAX_FILE_BYTES = 512 * 1024

# Evidence-like, media, archive, database and disk-image formats never belong in the repo.
FORBIDDEN_SUFFIXES = {
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".tif", ".tiff", ".bmp",
    ".mp4", ".mov", ".avi", ".mkv", ".wav", ".mp3", ".m4a", ".aac", ".flac",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".eml", ".msg", ".pst", ".mbox",
    ".zip", ".7z", ".rar", ".tar", ".gz", ".tgz",
    ".db", ".sqlite", ".sqlite3", ".dump", ".bak",
    ".e01", ".ex01", ".aff", ".dd", ".raw", ".img", ".iso", ".vmdk", ".mem", ".dmp",
    ".pem", ".key", ".p12", ".pfx",
}  # fmt: skip
FORBIDDEN_DIRS = {
    "node_modules",
    ".venv",
    "venv",
    "dist",
    "build",
    "coverage",
    "__pycache__",
}

SECRET_PATTERNS = {
    "private key block": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "AWS access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "GitHub token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
    "Slack token": re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"),
    "API secret key": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    # Allowed: CHANGE_ME / PASSWORD placeholders, ${VAR} interpolation, and explicit
    # "ci-only-" passwords for ephemeral CI service containers.
    "credential in URL": re.compile(
        r"postgres(?:ql)?(?:\+\w+)?://[^:\s/]+:(?!CHANGE_ME@|PASSWORD@|\$\{|ci-only-)[^@\s'\"]+@"
    ),
}

# Terms VERITAS deliberately does not use (duplicate concepts, verdict-like scoring,
# invented systems). Test files are exempt because they assert the absence of these terms.
FORBIDDEN_TERMS = re.compile(
    r"truth[ _-]?score|trust[ _-]?score|evidence[ _-]?(health|trust|readiness)"
    r"|(truth|trust|reality)[ _-]?engine|ai[ _-]?brain"
    r"|deep[ _-]?scan|smart[ _-]?scan|magic[ _-]?analysis",
    re.IGNORECASE,
)
TEXT_SUFFIXES = {
    ".py", ".ts", ".tsx", ".js", ".json", ".md", ".yml", ".yaml", ".toml", ".ini",
    ".html", ".css", ".sh", ".conf", ".mako", ".example", ".svg", "",
}  # fmt: skip


@cache
def repo_files() -> tuple[Path, ...]:
    out = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],  # noqa: S607
        cwd=ROOT,
        capture_output=True,
        check=True,
    ).stdout.decode()
    return tuple(ROOT / p for p in out.split("\0") if p and (ROOT / p).is_file())


def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def text_files() -> list[Path]:
    return [p for p in repo_files() if p.suffix in TEXT_SUFFIXES or p.name.startswith(".")]


def is_test_file(path: Path) -> bool:
    parts = path.relative_to(ROOT).parts
    return "tests" in parts or "test" in parts or ".test." in path.name


def test_no_environment_files_committed() -> None:
    offenders = [
        rel(p) for p in repo_files() if p.name.startswith(".env") and p.name != ".env.example"
    ]
    assert offenders == []


def test_env_example_holds_placeholders_only() -> None:
    values = dict(
        line.split("=", 1)
        for line in (ROOT / ".env.example").read_text().splitlines()
        if line and not line.startswith("#")
    )
    assert values["VERITAS_DEBUG"] == "false"
    assert values["POSTGRES_PASSWORD"] == "CHANGE_ME"
    assert ":CHANGE_ME@" in values["VERITAS_DATABASE_URL"]
    assert values["VERITAS_CORS_ORIGINS"] == ""
    assert "*" not in values["VERITAS_ALLOWED_HOSTS"]


# Backend security tests use obviously fake database URLs to prove they are never leaked.
TEST_FILE_EXEMPT = {"credential in URL"}


@pytest.mark.parametrize("name", sorted(SECRET_PATTERNS))
def test_no_secrets(name: str) -> None:
    pattern = SECRET_PATTERNS[name]
    candidates = [p for p in text_files() if not (name in TEST_FILE_EXEMPT and is_test_file(p))]
    offenders = [rel(p) for p in candidates if pattern.search(p.read_text(errors="ignore"))]
    assert offenders == []


def test_no_evidence_media_archives_or_databases() -> None:
    offenders = [rel(p) for p in repo_files() if p.suffix.lower() in FORBIDDEN_SUFFIXES]
    assert offenders == []


def test_no_generated_or_dependency_directories() -> None:
    offenders = [
        rel(p) for p in repo_files() if FORBIDDEN_DIRS.intersection(p.relative_to(ROOT).parts)
    ]
    assert offenders == []


def test_file_count_and_size_budget() -> None:
    files = repo_files()
    sizes = {rel(p): p.stat().st_size for p in files}
    assert len(files) <= MAX_FILES
    assert sum(sizes.values()) <= MAX_TOTAL_BYTES
    assert [name for name, size in sizes.items() if size > MAX_FILE_BYTES] == []


def test_no_forbidden_terminology() -> None:
    offenders = [
        rel(p)
        for p in text_files()
        if not is_test_file(p) and FORBIDDEN_TERMS.search(p.read_text(errors="ignore"))
    ]
    assert offenders == []


def test_frontend_source_never_addresses_backend_host_directly() -> None:
    src = ROOT / "frontend" / "src"
    offenders = [
        rel(p)
        for p in repo_files()
        if src in p.parents
        and not is_test_file(p)
        and re.search(r"localhost|127\.0\.0\.1", p.read_text(errors="ignore"))
    ]
    assert offenders == []


def test_demonstration_data_is_labelled() -> None:
    notice = "DEMONSTRATION DATA — NOT REAL EVIDENCE"
    assert notice in (ROOT / "backend" / "app" / "schemas.py").read_text()
    assert notice in (ROOT / "backend" / "app" / "seed.py").read_text()


def test_required_project_files_exist() -> None:
    required = [
        "README.md",
        "LICENSE",
        ".env.example",
        ".gitignore",
        "docker-compose.yml",
        "docker/backend.Dockerfile",
        "docker/frontend.Dockerfile",
        "docker/nginx.conf",
        ".github/workflows/ci.yml",
        "docs/architecture.md",
        "scripts/setup.sh",
        "scripts/dev.sh",
        "scripts/check.sh",
        "backend/pyproject.toml",
        "frontend/package-lock.json",
    ]
    assert [f for f in required if not (ROOT / f).is_file()] == []


def test_readme_covers_required_sections() -> None:
    readme = (ROOT / "README.md").read_text().lower()
    sections = [
        "scope", "architecture", "structure", "run", "environment", "docker",
        "testing", "security", "demonstration data", "limitations", "roadmap",
    ]  # fmt: skip
    assert [s for s in sections if s not in readme] == []
