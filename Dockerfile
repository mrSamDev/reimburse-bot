# --- Reimbursement Bot image ----------------------------------------------
# Requirements: slim base, non-root user, runtime env vars, temp filesystem,
# no secrets baked in. ~222MB — nixpacks produces a ~1GB image (Nix toolchain
# in a fat Ubuntu base), so a hand-written Dockerfile is used for size.

FROM python:3.12-slim

# Non-root user.
RUN groupadd --gid 1000 bot && useradd --uid 1000 --gid bot --create-home bot

WORKDIR /app

# Install dependencies first for better layer caching.
COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock

COPY app ./app

# Durable state dirs (SQLite ledger + sessions + backups) and tmpfs scratch,
# all owned by the non-root user. Named volumes inherit these on first mount.
RUN mkdir -p /app/data /app/backups /tmp/reimbursement \
 && chown -R bot:bot /app/data /app/backups /tmp/reimbursement

USER bot

# Runtime configuration comes exclusively from environment variables.
ENV TEMP_DIR=/tmp/reimbursement \
    PYTHONUNBUFFERED=1

CMD ["python", "-m", "app.main"]
