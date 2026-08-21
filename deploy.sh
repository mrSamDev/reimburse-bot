#!/usr/bin/env bash
# Build the image (Dockerfile, python:3.12-slim ~222MB) and deploy with
# docker compose. State (SQLite DBs) lives in named volumes and survives
# redeploys.
set -euo pipefail
cd "$(dirname "$0")"

docker compose build
docker compose up -d

echo "Deployed. Follow logs with: docker compose logs -f"
