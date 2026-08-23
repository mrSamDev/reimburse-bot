# Reimbursement Bot

A private, password-protected Telegram bot that collects receipt photos and turns them into a PDF reimbursement report with a vision AI provider. It runs live at [@reimbursement_mrsamdev_bot](https://t.me/reimbursement_mrsamdev_bot), is MIT-licensed, and is shared as a reference implementation.

## What it does

An authorized user sends receipt photos, or JPEG, PNG, and WEBP image documents. The bot stages each photo's `file_id` in a per-user SQLite session. That's all it holds, the `file_id`. The image bytes stay in Telegram and nothing gets downloaded yet.

On `/generate` the user sets a report heading and enters the password. The job is enqueued and a background worker pool drains it. Each worker downloads an image to a tmpfs, normalizes it, sends it to a vision model (OpenAI, Ollama, or both), validates what comes back, embeds everything into a ReportLab PDF with a `Decimal` total, and sends the PDF back.

Everything accepted or failed also lands in a durable audit ledger that gets backed up at startup.

Here's the whole trust model. Receipts never leave Telegram until `/generate` plus the correct password. Because the bot long-polls Telegram's API outbound, it needs no public URL, no port forwarding, no ngrok. It sits quietly behind a firewall.

## Architecture

```
Telegram <--outbound long-poll--> app/main.py (PTB Application)
                                      │
                      ┌───────────────┴────────────────────────────┐
                      │ ReimbursementBot (bot/)                     │
                      │  CommandHandlersMixin + ReceiptInputMixin    │
                      │  JobQueue → JobProcessor workers             │
                      │  UserLockManager, PasswordThrottle           │
                      └───────────────┬──────────────────────────────┘
                                      │
                     ProcessingService (services/receipt_service/) ── retry/pipeline/run/types
                       │  download → validate → normalize → AI extract → validate → PDF
                       ▼
             AI layer (ai/)  ReceiptVisionProvider (ABC)
               ├── OpenAIProvider (openai SDK, gpt-4o-mini)
               ├── OllamaProvider (OpenAI-compatible /v1, llava)
               └── ProviderPool (round_robin | priority)
                       ▼
              validation.py + financial_validation.py + models/receipt.py
                       ▼
              pdf_service.py (ReportLab) → PDF → TelegramService.send_document

Persistent side-stores:
  sessions.db  (per-user state machine + atomic staging, WAL)   → SessionStore
  receipts.db  (audit ledger, dedup by file_id)                 → ReceiptLedger
  backups/     (online-backup API copies, retention-pruned)
```

`app/` runs about 4,520 lines, `tests/` about 5,273, with 393 test functions across three authors. The rationale for each call, from the queue to the tmpfs sizing, lives in [docs/design-decisions.md](design-decisions.md).

## Key design decisions

**Receipts never leave Telegram until generation.** This one choice makes the whole thing deployable with zero inbound exposure. Only `file_id`s live in SQLite. Images get pulled into a tmpfs transiently and deleted in a `finally` block, with a startup sweep that clears anything a hard kill left behind.

**The AI reads, the app owns the arithmetic.** Raw model output is untrusted, so it passes three gates. First, a Pydantic shape that checks type and coerces money through `Decimal(str(...))` so binary floats can't sneak in. Second, hard rules: a merchant name must exist, a total must be present, and the result becomes a strict `Receipt`. Third, business checks that reconcile `subtotal + tax - discount` against the total within two cents, flag low confidence, and note a missing date. Those last checks never throw. They set `review_required` and append a warning for a human. Totals are summed per currency as `Decimal`, and the PDF never mixes currencies.

**Bounded concurrency, not a wall.** The in-memory FIFO queue is drained by `WORKER_COUNT` background workers. `/generate` replies immediately with your place in line. The queue is bounded, so when it's full the user gets `QUEUE_FULL` and keeps their staged receipts. The global cap is `WORKER_COUNT` times `AI_CONCURRENCY`, which comes to 2 by default. Those are the only two concurrency dials, on purpose. Inside a batch, receipts are extracted concurrently under a semaphore with a small delay between calls. Per-user serialization uses one `asyncio.Lock` per user plus an atomic `processing` flag in SQLite, so a single user can't run two jobs at once while different users proceed in parallel.

Scaling here is a wait-time dial. With `W` workers, the last user waits about `total_receipts / W` times the per-receipt cost. Raise `WORKER_COUNT` up to your provider's rate limit, and the `pool` provider adds a second lane. Under load the system gets slower, it never breaks.

**Provider pool.** With `AI_PROVIDER=pool`, OpenAI and Ollama Cloud run at once. Round robin spreads receipts across both for throughput and falls back when one fails. Priority tries the primary first and falls back on failure or low confidence. Both providers run with the SDK's auto-retry off, so the app's own backoff is the only retry layer. That backoff reads a 429, distinguishes a tokens-per-minute window from a generic rate limit, applies full jitter, and respects a per-batch call budget so a runaway report can't drain your OpenAI account.

**Timeouts and budgets.** A per-receipt hard timeout, a whole-batch soft budget with a hard abort, an AI call budget, and a rule that a single failing receipt is isolated so the batch lives on.

**Durability and crash recovery.** The session store is SQLite in WAL with migrations. Staging uses an atomic SQL append. The audit ledger deduplicates by `file_id` and records the delivery outcome, so a re-run after a crash never double counts. Because the queue is in-memory, a restart loses queued jobs. That's accepted, and it's mitigated: on startup, affected users are told their job was lost and their sessions reset to idle so they can re-run `/generate`. Both databases are backed up at startup through the SQLite online-backup API.

**Single instance, by design.** A `flock` on `data/instance.lock` stops a second poller on the same token. The kernel releases it on SIGKILL or OOM. A duplicate container fails fast with an exit code instead of a 409 conflict on `getUpdates`.

**Observability.** A zero-dependency HTTP server serves `/health` (always open) and `/metrics` (token-gated). Metrics are in-process counters plus count-and-sum durations, reset on restart. Logging is structured, `text` or `json`, correlated by request id.

## State machine

```
IDLE ──send receipt──► COLLECTING ──/generate──► AWAITING_HEADING
                                                    │ set heading
                                                    ▼
                                              AWAITING_PASSWORD
                                                    │ correct
                                                    ▼
                                                QUEUED ──worker──► PROCESSING ──► IDLE
```

`/cancel` only aborts the heading or password flow; elsewhere it's a no-op that keeps your staged receipts. Input during `PROCESSING` or `QUEUED` gets a busy reply. `/clear` drops the staged receipts, and duplicate uploads are rejected. The password is checked with a constant-time `hmac.compare_digest`, and the message is deleted from chat the moment it's read. Wrong attempts trip a per-user in-memory throttle that resets on restart.

## Deployment

A hand-written `Dockerfile` on `python:3.12-slim` builds about a 222 MB image, deliberately not the ~1 GB you'd get from Nixpacks. It pins Python 3.12, installs the hash-pinned `requirements.lock`, runs as a non-root user on a tmpfs, and takes secrets from `.env` at runtime so nothing gets baked in. Durable state lives in named Docker volumes and survives rebuilds.

`docker-compose.yml` uses `restart: unless-stopped`, a 512 MB tmpfs (up from 64 MB, which real users overflowed), and named `data` and `backups` volumes. No `container_name` is set, because that would break Dokploy's logs and metrics. `deploy.sh` builds and starts it in one step.

To deploy, clone the repo, copy `.env.example` to `.env`, fill in the token, allow-lists, password, and provider keys, then run `./deploy.sh`. Upgrade after a code change with `git pull` and `./deploy.sh` again; the volumes keep your state. The single-instance `flock` sits on the shared `data` volume, so a duplicate container sharing it fails fast rather than fighting over `getUpdates`.

## Tests and quality

The suite runs 393 tests, unit and integration, all offline, because both I/O boundaries are faked. That covers providers, retry and backoff, concurrency, the PDF, the health server, file validation, metrics, auth, the state machine, financial validation, the receipt model, sessions, cleanup, the job queue, request logging, the telegram service, batch processing, the bot surface, currency, backups, bot logic, throttling, captions, config, the report period, the ledger, the pool, logging redaction, and main.

`ruff` and `mypy` run as pre-commit hooks along with the full `pytest`. Runtime dependencies are pinned by hash in `requirements.lock`, while dev tools stay unpinned in `requirements-dev.txt`. Coverage on the core modules sits at 91 to 93 percent.

## Configuration

All values come from the environment. The essentials are `TELEGRAM_TOKEN`, `ALLOWED_USER_IDS` and `ALLOWED_CHAT_IDS` (a default-deny allow-list), `BOT_PASSWORD`, and the provider settings (`AI_PROVIDER` of `openai`, `ollama`, or `pool`, plus `AI_POOL_STRATEGY` and `AI_POOL_PRIMARY`). Tuning knobs include `MAX_RECEIPTS` (20), `MAX_FILE_SIZE_MB` (10), `IMAGE_MAX_EDGE` (1024 pixels, which controls token use and 429 risk), `AI_CONCURRENCY`, `WORKER_COUNT`, `MAX_QUEUE_SIZE`, the retry and backoff values, several timeouts, `SESSION_LEASE_TTL_SECONDS`, `MAINTENANCE_INTERVAL_SECONDS`, `HEALTH_ENABLED`, `HEALTH_PORT`, and `HEALTH_TOKEN`, `REPORT_TITLE`, and `LOG_FORMAT`. The report period subtitle, like "July Expenses", is derived from the dominant transaction month, not from the environment.

## Security model and known limitations

This is a small, private bot, not a general-purpose auth system. Authorization is a static allowlist with everyone else denied. The password is a single shared plaintext secret sent through chat history and stored plaintext in the environment. It's throttled per user, but the throttle is in memory and resets on restart, and the secret is still a shared chat-transported value. Receipts stay in Telegram until `/generate`; the server only holds `file_id`s. The health and metrics server is unauthenticated on `/health` and token-gated on `/metrics`, over plaintext HTTP on `0.0.0.0`, so keep it behind a firewall. The report period subtitle trusts AI-extracted dates, and only when confidence is high.

That model is fine for two people who trust each other. It is not a hardened multi-tenant credential system.

## Commands

`/start`, `/help`, `/status` (shows staged count), `/clear`, `/generate` (heading, then password, then queued to PDF), and `/cancel`. Uploads are photos or JPEG, PNG, and WEBP documents.

## Recent engineering history

In rough order from most recent: input handling moved into mixins, command handlers moved into mixins, the queue worker became a standalone `JobProcessor`, and the receipt service split into a package. Coverage on OpenAI and main rose to about 93 percent. The cross-process lease was dropped for a single-process session store. The startup sweep became age-checked so it never deletes a fresh in-flight directory. Users are now told when a restart lost their queued job, and the queue is bounded. The `Batch.total` cross-currency footgun was removed, the session purge loop became one indexed `DELETE`, and the provider pool arrived so OpenAI and Ollama can run together. `/generate` jobs were queued to background workers, and the tmpfs grew to 512 MB.

## Strengths

Pure logic is cleanly separated from the PTB glue, so the core decisions are testable without Telegram. Defense runs deep on both the AI path (shape, hard validation, review, Decimal arithmetic) and the concurrency path (queue, worker count, per-user locks and flags, a semaphore, timeouts, and a call budget). The tests are strong and run offline, and the docs spell out the architecture, config, security model, and checklist. The operational choices are considered: the single-instance flock, named-volume durability, tmpfs sizing, backup and restore, and health and metrics.

## Weaknesses and rough edges

There's no distributed support. It's single-instance only, so scaling means webhooks, shared state, and a distributed queue. The shared plaintext password and the chat-transported secret are acceptable only for trusted pairs. The in-memory queue and throttle lose state on restart, though notification and reset soften the blow. Metrics live in process and reset, with no history. Adding a user means editing the allow-list and redeploying. Currency defaults to AED, and totals stay per currency with no FX conversion.

---

*Source: `telegram-reimbursement-bot` (mrSamDev). `app/` about 4,520 lines, `tests/` about 5,273 lines, 393 tests.*
