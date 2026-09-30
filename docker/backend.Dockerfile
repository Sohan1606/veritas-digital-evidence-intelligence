# VERITAS API image. Build context: repository root.
FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore

WORKDIR /srv/backend

# Runtime dependencies (no dev extras) from pyproject.toml alone, so this layer is
# cached across application-code changes.
COPY backend/pyproject.toml ./
RUN python -c "import tomllib; print('\\n'.join(tomllib.load(open('pyproject.toml', 'rb'))['project']['dependencies']))" > /tmp/requirements.txt \
 && pip install -r /tmp/requirements.txt && rm /tmp/requirements.txt \
 && useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin veritas \
 && mkdir -p /var/lib/veritas/evidence \
 && chown -R veritas:veritas /var/lib/veritas/evidence

COPY backend/app ./app
COPY backend/migrations ./migrations
COPY backend/alembic.ini ./
RUN pip install --no-deps .

USER veritas
EXPOSE 8000

# Liveness only; readiness (/ready) also checks the database and schema revision.
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).status == 200 else 1)"]

# Apply migrations, then serve. Demonstration data is never loaded by default.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:get_app --factory --host 0.0.0.0 --port 8000 --no-server-header"]
