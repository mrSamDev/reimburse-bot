# Reimbursement Bot

A private, password-protected Telegram bot that collects receipt photos and turns them into a PDF reimbursement report through a vision AI provider.

Try it: **[t.me/reimbursement_mrsamdev_bot](https://t.me/reimbursement_mrsamdev_bot)** — @reimbursement_mrsamdev_bot

The bot is restricted to an allowance list. To use it, email your **Telegram USER_ID** to **sijo@sijosam.com** and you'll be added.

<img src="assets/telegram-qr.png" alt="QR code linking to @reimbursement_mrsamdev_bot" width="300"/>

## How it works

Receipts never leave Telegram until you ask for a report. The bot holds only each photo's `file_id` in a staging session, and downloads nothing until `/generate` plus the correct password. That's the whole trust model, so we don't need a public URL, port forwarding, or ngrok. It long-polls Telegram's API outbound and stays quietly behind your firewall. Details live in [`docs/telegram-connection.md`](docs/telegram-connection.md).

The AI does the reading, but the app owns the arithmetic. Every total is computed with Python `Decimal`, and AI output passes a Pydantic schema plus business rules before it's trusted. Raw model output never reaches the PDF layer.

With `AI_PROVIDER=pool`, OpenAI and Ollama Cloud run at the same time: `round_robin` spreads receipts across both for throughput, or `priority` prefers one and falls back to the other on failure or low confidence. This roughly doubles the AI throughput ceiling and adds redundancy if one provider is down.

Storage is temporary and request-scoped. Images and the PDF sit under `temp/request_<id>/` and get deleted in a `finally` block even when something fails, while a startup sweep clears orphans left by a crash. In Docker the temp root is a 512m tmpfs, sized for several concurrent batches (each keeps raw + normalized images until the PDF is delivered).

State is durable. Per-user staging sessions and the cross-process per-user lease live in SQLite (`data/sessions.db`, WAL mode), so restarts don't lose anything and generation stays serialized across instances. A background sweep reclaims stale sessions and crashed leases.

Generation is queued, not blocking. `/generate` (after the password) enqueues a job and replies immediately with your position in line; a pool of `WORKER_COUNT` background workers drains the queue, so concurrent users are processed with bounded parallelism instead of hammering the AI provider. The queue is **in-memory** — a restart drops queued jobs, notifies the affected users, and resets those sessions to idle (they can re-run `/generate`).

Scaling is a wait-time dial, not a wall. With `W` workers, the last user waits roughly `total_receipts / W × time_per_receipt`. Raise `WORKER_COUNT` (up to your AI provider's rate limit) to cut wait time; the provider pool (`AI_PROVIDER=pool`) adds a second lane. The system degrades gracefully under load — it gets slower, never breaks.

There's also an audit ledger. Every accepted and failed receipt lands in SQLite (`data/receipts.db`), deduplicated by Telegram `file_id`, with the delivery outcome recorded. Reimbursements keep a persistent trail no matter how often the bot restarts.

## Requirements

- Python 3.10+
- A Telegram bot token (via @BotFather)
- An OpenAI API key **or** an Ollama (vision) endpoint
- An allowance list of numeric Telegram user IDs

## Setup

```bash
cp .env.example .env      # then fill in values
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pytest                     # run the test suite
python -m app.main         # start long polling
```

## Configuration

| Variable | Description |
|----------|-------------|
| `TELEGRAM_TOKEN` | Bot token from @BotFather |
| `ALLOWED_USER_IDS` | Comma-separated Telegram user IDs allowed to use the bot |
| `ALLOWED_CHAT_IDS` | Optional comma-separated chat ID allow-list |
| `BOT_PASSWORD` | Password required before generating a report |
| `AI_PROVIDER` | `openai`, `ollama`, or `pool` (both at once) |
| `AI_POOL_STRATEGY` | Pool strategy: `round_robin` (both lanes used) or `priority` (primary first, fallback on failure/low-confidence) (default `round_robin`) |
| `AI_POOL_PRIMARY` | Primary provider for `priority` strategy: `openai` or `ollama` (default `ollama`) |
| `OPENAI_API_KEY` / `OPENAI_MODEL` | OpenAI credentials |
| `OLLAMA_BASE_URL` / `OLLAMA_MODEL` | Ollama vision endpoint + model |
| `MAX_RECEIPTS` | Max receipts per report (default 20) |
| `MAX_FILE_SIZE_MB` | Max image size (default 10) |
| `IMAGE_MAX_EDGE` | Longest image edge sent to the vision AI (px). Lower = fewer tokens = less 429 risk (default 1024) |
| `TEMP_DIR` | Temporary processing root |
| `DATA_DIR` | Persistent data root (holds the `receipts.db` audit ledger) |
| `AI_RETRY_ATTEMPTS` | Retries on transient AI failures (default 3) |
| `AI_RETRY_BASE_DELAY` | Backoff seconds between AI retries (default 1.0) |
| `AI_REQUEST_DELAY_SECONDS` | Pause between consecutive receipts; 0 disables (default 1.0) |
| `AI_CONCURRENCY` | Max receipts extracted in parallel within one batch (default 1, one at a time) |
| `WORKER_COUNT` | Background workers draining the job queue; the global cap on concurrent batches (default 2) |
| `MAX_PROCESSING_SECONDS` | Soft whole-batch time budget, 0 disables (default 600) |
| `SESSION_LEASE_TTL_SECONDS` | Seconds before a crashed generation's processing lease is reclaimable (default 120) |
| `MAINTENANCE_INTERVAL_SECONDS` | Background lease-reclaim + session-purge sweep interval (default 60) |
| `AI_PER_RECEIPT_TIMEOUT_SECONDS` | Hard per-receipt processing timeout (default 120) |
| `AI_MAX_CALLS_PER_RUN` | Max paid AI extraction calls per report; the batch aborts when exceeded (default 100) |
| `LOG_FORMAT` | `text` or `json` structured logs (default `text`) |
| `BACKUP_DIR` | Directory for durable DB backups (default `backups/`) |
| `BACKUP_RETENTION` | Max backup copies kept per database; older ones pruned at startup (default 10) |
| `HEALTH_ENABLED` | Serve `/health` + `/metrics` on `HEALTH_PORT` (default `false`) |
| `HEALTH_PORT` | HTTP port for the health/metrics server (default 8080) |
| `REPORT_TITLE` | Report header title (default `Heading Travel Expenses`) |

> The report **period** subtitle (e.g. "July Expenses") is derived automatically from the receipts' transaction dates (dominant month across the batch), not configured via env.

## Commands

| Command | Behaviour |
|---------|-----------|
| `/start` | Start the bot |
| `/help` | Show help |
| `/status` | "Receipts staged: N" |
| `/clear` | Clear staged receipts |
| `/generate` | Ask for a report heading + password, then build & send the PDF |
| `/cancel` | Cancel the current flow (heading or password) |

Send a photo or a JPEG/PNG/WEBP image document to stage a receipt.

## Docker (VPS deployment)

A hand-written `Dockerfile` (python:3.12-slim, ~222MB) builds the image — not Nixpacks (Nixpacks would pull in a fat Ubuntu base with the Nix toolchain and produce a ~1GB image). The image pins Python 3.12, installs the hash-pinned `requirements.lock`, and runs as a non-root user (uid 1000) on a tmpfs for temporary files. Secrets come in at runtime via `.env` and never get baked into the image. Durable state (`receipts.db`, `sessions.db`, plus their backups) lives in named Docker volumes, so it survives rebuilds.

### Deploy on a VPS

1. Copy the repo to the VPS and enter it:
   ```bash
   git clone https://github.com/mrSamDev/reimburse-bot
   cd telegram-reimbursement-bot
   ```
2. Create your secrets file from the template (never commit it):
   ```bash
   cp .env.example .env
   # edit .env, set TELEGRAM_TOKEN, ALLOWED_USER_IDS, ALLOWED_CHAT_IDS,
   # BOT_PASSWORD, and the AI provider settings (OPENAI_API_KEY or OLLAMA_*)
   ```
3. Build and start in one shot (`docker compose build` then `docker compose up`):
   ```bash
   ./deploy.sh
   ```
   Or manually:
   ```bash
   docker compose build
   docker compose up -d
   ```
4. Watch startup logs (first poll, DB init, and backup happen here):
   ```bash
   docker compose logs -f
   ```
   The container restarts automatically (`restart: unless-stopped`).

> **Single-instance guard**: the bot holds a flock on `data/instance.lock`
> (on the shared `data` volume) for its whole lifetime. A second instance —
> another container sharing the volume, or a stray local run against the same
> `DATA_DIR` — fails fast with `exit 1` and a clear "another bot instance is
> already running" log line instead of 409-conflicting on Telegram's
> `getUpdates`. That crash-loop is the loud signal that a duplicate container
> exists; remove the duplicate (set `replicas: 1`, stop extra containers),
> don't try to outrun it.

### Upgrade after a code change

```bash
git pull
./deploy.sh        # rebuild image, reuse existing volumes (state preserved)
```

To inspect persisted state:

```bash
docker volume inspect reimbursement-bot_data
docker compose exec bot sh -c 'ls -la /app/data /app/backups'
```

## Tests

```bash
pytest                       # full suite (unit + integration, all mocked)
```

Runtime dependencies are hash-pinned in `requirements.lock` (generated with `pip-compile --generate-hashes`); dev tools (`ruff`, `mypy`, `pip-audit`, `pytest`) are installed unpinned in CI / `requirements-dev.txt`.

Integration tests use fakes for Telegram and the AI provider, so the whole suite runs offline.

## Project layout

A word on structure. The plan (its §7) suggests separate `bot/commands.py`, `bot/handlers.py`, `services/processing_service.py`, and `models/batch.py`. For a single-module pipeline we deliberately consolidated those. PTB handlers live in `bot/bot.py` with the pure decision logic split out into `bot/logic.py` for testability. The orchestration pipeline sits in `services/receipt_service.py`, and `Batch` lives alongside `Receipt` in `models/receipt.py`. Each subsystem stays cohesive without changing behaviour.

```
app/
  main.py           entrypoint / PTB wiring
  config.py         env-driven configuration
  bot/              commands, handlers, state machine, message text
  services/         processing, pdf, telegram, security, cleanup, validation
  ai/               provider abstraction + openai/ollama + validation
  models/           Receipt, Batch, Session
  prompts/          vision extraction prompt
  utils/            files, images, logging
tests/
  unit/             per-module tests
  integration/      pipeline, telegram flow, reference-output tests
  fixtures/         images, ai responses, reference dataset
```

## Backups & restore

The state (`data/sessions.db`) and audit (`data/receipts.db`) databases get backed up to `backups/` at startup via the SQLite online-backup API. To restore, stop the bot, copy a `*_sessions_*.db` or `*_receipts_*.db` file over the live database, and start it again:

```bash
cp backups/receipts_receipts_20240101_120000.db data/receipts.db
```

## Health & metrics

With `HEALTH_ENABLED=true`, a zero-dependency HTTP server serves:
- `GET /health` → `{"status":"ok"}` — always open (liveness probe)
- `GET /metrics` → JSON of the in-process counters + durations (`processed`, `review`, `failed`, `delivered`, `ai_calls`, `ai_errors`, `receipt_processing_seconds_count/sum`, `batch_processing_seconds_count/sum`, and the failure classes `timeout`/`validation_error`/`ai_error`/`unexpected`)

If `HEALTH_TOKEN` is set, `GET /metrics` requires `Authorization: Bearer <token>`; `/health` stays open. The server binds `0.0.0.0` over plain HTTP — keep the port behind your firewall.

> Metrics are held in-process and **reset on restart**, so they describe the current run, not history.

## Security model & known limitations

This is a small, private bot, not a general-purpose auth system. Be aware of what it does and does not protect:

- **Authorization** is a static allowlist (`ALLOWED_USER_IDS`); everyone else is default-denied.
- **The report password is a single shared plaintext secret** sent by the user through the Telegram chat (whose history is retained). `BOT_PASSWORD` is stored in plaintext in the env/config. A wrong password is throttled per user (`PASSWORD_MAX_ATTEMPTS` / `PASSWORD_LOCKOUT_SECONDS`), but the throttle is **in-memory and resets on restart**, and the secret itself is still a shared, chat-transported value — not a per-user credential.
- **Receipts stay in Telegram** until `/generate`; the server only holds `file_id`s in SQLite. Images are downloaded transiently to a tmpfs and deleted after processing.
- The health/metrics server (if enabled) is unauthenticated on `/health` and token-gated on `/metrics`, over plaintext HTTP on `0.0.0.0`.
- AI-extracted receipt data is validated before use, but the report **period subtitle is derived from AI-extracted transaction dates** and trusts them only when extraction confidence is high.

This model is fine for two people who trust each other; it is **not** a hardened multi-tenant credential system.

## Production checklist

- `ALLOWED_USER_IDS` is populated, so everyone else is default-denied.
- `BOT_PASSWORD` is set and strong.
- Secrets live only in `.env`, never in Git, the image, or logs.
- The report PDF embeds the original receipt image per row, preserves aspect ratio, breaks across pages, and ends with a `Decimal`-computed total.

## License

Released under the [MIT License](LICENSE).

> Personal tool shared as a reference implementation. This bot is not a hardened multi-tenant credential system; run it only for people you trust (see [Security model](#security-model--known-limitations)).
