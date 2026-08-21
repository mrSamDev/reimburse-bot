# --- Reimbursement Bot image ----------------------------------------------
# Requirements: slim base, non-root user, runtime env vars, temp filesystem,
# no secrets baked in.

FROM python:3.12-slim

# Non-root user.
RUN groupadd --gid 1000 bot && useradd --uid 1000 --gid bot --create-home bot

WORKDIR /app

# Install dependencies first for better layer caching.
COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock

COPY app ./app

# Temporary storage on a tmpfs volume (declared in compose); owned by non-root.
RUN mkdir -p /tmp/reimbursement && chown -R bot:bot /tmp/reimbursement

USER bot

# Runtime configuration comes exclusively from environment variables.
ENV TEMP_DIR=/tmp/reimbursement \
    PYTHONUNBUFFERED=1

CMD ["python", "-m", "app.main"]
