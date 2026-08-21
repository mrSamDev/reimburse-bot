# Reimbursement Bot

A private, password-protected Telegram bot that collects receipt photos and
generates a PDF reimbursement report via a vision AI provider.

## Key design points

- **Receipts stay in Telegram.** Only Telegram `file_id` values are held in
  staging sessions. Files are downloaded only after `/generate` + correct
  password.
- **AI owns extraction, the app owns arithmetic.** All totals are computed with
  Python `Decimal`. AI output is validated (Pydantic schema + business rules)
  before use, and never passed raw to the PDF layer.
- **Temporary, request-scoped storage.** Images, normalized images and the PDF
  live under `temp/request_<id>/` and are deleted in a `finally` block even on
  failure (and orphans from a crash are swept at startup).
- **Durable state layer.** Per-user staging sessions and the cross-process
  per-user processing lease live in SQLite (`data/sessions.db`, WAL), so state
  survives restarts and generation is serialized across instances. Stale
  sessions and crashed leases are purged at startup.
- **Durable audit ledger.** Every accepted and failed receipt is recorded in
  SQLite (`data/receipts.db`), deduplicated by Telegram `file_id`, with
  delivery outcome, so reimbursements have a persistent trail across restarts.

## Requirements

- Python 3.10+
- A Telegram bot token (via @BotFather)
- An OpenAI API key **or** an Ollama (vision) endpoint
- An allowance list of numeric Telegram user IDs

## How it connects to Telegram

The bot uses **long polling** — it connects *out* to Telegram's API, so it needs
no public URL, no port forwarding, and **no ngrok**. Read
[`docs/telegram-connection.md`](docs/telegram-connection.md) for the details.

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
| `AI_PROVIDER` | `openai` or `ollama` |
| `OPENAI_API_KEY` / `OPENAI_MODEL` | OpenAI credentials |
| `OLLAMA_BASE_URL` / `OLLAMA_MODEL` | Ollama vision endpoint + model |
| `MAX_RECEIPTS` | Max receipts per report (default 20) |
| `MAX_FILE_SIZE_MB` | Max image size (default 10) |
| `TEMP_DIR` | Temporary processing root |
| `DATA_DIR` | Persistent data root (holds the `receipts.db` audit ledger) |
| `AI_RETRY_ATTEMPTS` | Retries on transient AI failures (default 3) |
| `AI_RETRY_BASE_DELAY` | Backoff seconds between AI retries (default 1.0) |
| `AI_CONCURRENCY` | Max receipts extracted in parallel (default 2) |
| `MAX_PROCESSING_SECONDS` | Soft whole-batch time budget, 0 disables (default 300) |
| `SESSION_LEASE_TTL_SECONDS` | Seconds before a crashed generation's processing lease is reclaimable (default 120) |
| `AI_PER_RECEIPT_TIMEOUT_SECONDS` | Hard per-receipt processing timeout (default 120) |
| `LOG_FORMAT` | `text` or `json` structured logs (default `text`) |
| `REPORT_TITLE` / `REPORT_PERIOD` | Report header metadata |

## Commands

| Command | Behaviour |
|---------|-----------|
| `/start` | Start the bot |
| `/help` | Show help |
| `/status` | "Receipts staged: N" |
| `/clear` | Clear staged receipts |
| `/generate` | Ask for the password, then build + send the PDF |
| `/cancel` | Cancel the password flow |

Send a photo or a JPEG/PNG/WEBP image document to stage a receipt.

## Docker

```bash
docker compose up --build -d
```

The image runs as a non-root user with a tmpfs for temporary files. Secrets are
injected at runtime via `.env` and are never baked into the image.

## Tests

```bash
pytest                       # full suite (unit + integration, all mocked)
```

Integration tests use fakes for Telegram and the AI provider, so the suite runs
offline.

## Project layout

> **Note on structure.** The plan (§7) suggests separate `bot/commands.py`,
> `bot/handlers.py`, `services/processing_service.py` and `models/batch.py`. For
> a single-module pipeline we consolidated these deliberately:
> PTB handlers live in `bot/bot.py` (with pure decision logic in
> `bot/logic.py` for testability), the orchestration pipeline lives in
> `services/receipt_service.py`, and `Batch` lives alongside `Receipt` in
> `models/receipt.py`. This keeps each subsystem cohesive without changing any
> behaviour.

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

## Production checklist

- `ALLOWED_USER_IDS` is populated (default-denies everyone else).
- `BOT_PASSWORD` is set and strong.
- Secrets live only in `.env`, never in Git/image/logs.
- The report PDF embeds the original receipt image per row, preserves aspect
  ratio, breaks across multiple pages, and ends with a `Decimal`-computed total.
