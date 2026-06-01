# Aether web image — runs BOTH web interfaces from one image:
#   * Stargazer        (read-only dashboard)         python -m aether.stargazer.server
#   * Operator panel   (privileged control plane)    python -m aether.operator_panel.server
# docker-compose.yml picks the entrypoint per service via `command:`.
#
# `aether` is a PEP 420 namespace package (no aether/__init__.py), so it just
# needs to sit on sys.path under /app — WORKDIR /app + `python -m aether.*`.
FROM python:3.11-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Install deps first so the layer caches across code changes.
COPY requirements.txt /app/aether/requirements.txt
RUN pip install --no-cache-dir -r /app/aether/requirements.txt

# Then the package itself (.dockerignore keeps tests/docs/.env/__pycache__ out).
COPY . /app/aether/

# Default entrypoint = Stargazer; the operator service overrides `command:`.
CMD ["python", "-m", "aether.stargazer.server"]
