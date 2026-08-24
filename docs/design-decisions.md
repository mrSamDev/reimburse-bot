# Design decisions

A log of the calls we made, roughly in the order we made them. Each entry says what we chose, why, and what we turned down. Git history is the source of truth; this is the reasoning behind it.

## A job queue that backs off, not a wall

`/generate` has to download every image, ask a vision model about each receipt, and build a PDF. Do that inline and one user blocks the whole event loop, while several users hammer the AI provider at once.

So the handler never does the work. After the password check, it enqueues a job and replies immediately with your place in line. A pool of background workers drains the queue. `WORKER_COUNT` is the cap on concurrent batches, and `AI_CONCURRENCY` caps parallelism inside a batch. Those two are the only dials we let anyone touch. Add a third and you're just buying race conditions.

The queue is bounded. When it's full, enqueue fails and the user is told to try again, with their staged receipts still intact so they don't re-upload anything. That's a graceful "come back in a minute", not an OOM.

This makes scaling a wait-time question rather than an availability one. With W workers, the last user waits roughly `total_receipts / W` times the per-receipt cost. Raise the worker count until you hit your provider's rate limit. The pool (below) adds a second lane. The system gets slower under load, it never breaks.

## Losing jobs on restart is fine, as long as they know

The queue lives in memory only. A restart drops it. We accepted that. A durable queue is a big complexity tax for a small private bot, and an unfinished report is cheap to regenerate because the receipts are still staged.

But we don't leave people hanging. On startup, before any worker starts, we find sessions stuck in `QUEUED` or `PROCESSING`, tell each user their job was lost, and reset them to idle so they can re-run `/generate`. That reset runs first for a reason: let it run while workers are live and it would clear the processing flag on a job that's actually running.

## One process, one store, no lease

Session state has to survive restarts and stay concurrency-safe. Ours lives in a single-process SQLite store in WAL mode, with schema migrations. `get()` hands back a detached snapshot; you mutate it, then `save()` persists it.

This is safe only because a `flock` on `data/instance.lock` guarantees one running instance, so there's no cross-process coordination to do. That is exactly why we deleted the cross-process lease we used to keep. The migration still runs the old add-then-drop so existing databases come along quietly.

Within the process, `try_acquire_processing` atomically claims a per-user slot and always releases it in a `finally`. A `UserLockManager` keeps one `asyncio.Lock` per user, so the same user can't overlap two generations while different users proceed in parallel.

## Run two AI providers at once

With `AI_PROVIDER=pool`, OpenAI and Ollama Cloud run at the same time behind one `ProviderPool` implementing the same provider interface. Round robin spreads receipts across both for throughput and falls back when one fails. Priority tries the primary first and falls back on failure or on low confidence, so a shaky reading from one model gets a second opinion from the other.

That roughly doubles the throughput ceiling and gives you a spare when a provider goes down.

Both providers run with the SDK's auto-retry switched off, so our own backoff is the only retry layer. Let the SDK and the app both pace and you get two people turning the same dial. Ollama exposes an OpenAI-compatible endpoint, so it reuses the openai SDK and shares the JSON extraction and Retry-After parsing with OpenAI.

## One retry layer, and a budget to keep you honest

Retries live in one provider-agnostic module. It retries only transient provider errors; a validation failure is not going to fix itself. It reads a 429 and tells a tokens-per-minute limit apart from a generic rate limit, waits out the sixty-second TPM window for the former and honors `Retry-After` for the latter, and never sleeps past the per-receipt deadline.

It also counts paid calls against a per-run budget. Blow the budget and the batch aborts instead of bleeding your OpenAI bill into next week on retries.

## The AI reads, the app does the arithmetic

Raw model output is never trusted. It passes three gates. First, a Pydantic shape that only checks type, where money is coerced through `Decimal(str(...))` so binary floats can't sneak in. Second, hard rules: a merchant name must exist, a total must be present, currency must match a three-letter pattern, confidence must land in `[0,1]`. Third, business checks that reconcile `subtotal + tax - discount` against the total within two cents, and flag low confidence or a missing date.

Those last checks never throw. They set `review_required` and append a note so a human decides. A mismatch is a reason to look, not a reason to crash the batch.

Totals are `Decimal` per currency. We do not combine them, ever.

## The cross-currency footgun

There used to be a single `Batch.total`. We removed it. Summing AED plus USD plus EUR into one number is a silent correctness bug, and it's the kind that sails past review because it looks fine in the PDF.

So `Batch` keeps a `Decimal` total per currency and the PDF renders one line per currency. Multi-currency reports just list each currency's total. The document's metadata title does the same.

## Fail fast, don't 409

The bot takes a non-blocking `flock` on `data/instance.lock` for its whole life. A second instance, a duplicate container on the shared volume, or a stray local run on the same data directory, fails fast with an exit code and a log line. No half-started poller, no silent update drops.

We chose `flock` because the kernel releases it on SIGKILL or OOM. No stale locks, no heartbeat, no cleanup. If you see a container crash-looping, that's the signal that a duplicate exists. Remove it. Don't try to outrun it.

## Scrub the scratch space

Each batch runs in its own `temp/request_<id>/` directory, and cleanup deletes the whole tree in a `finally`, even on failure. A startup sweep then mops up anything a hard kill left behind.

The sweep is age-checked. It only removes directories older than a few minutes, so a slow worker still writing its own directory never has its work deleted under it. The zero option removes everything and is only safe on a per-container tmpfs.

In Docker the temp root is a 512m tmpfs, up from 64m after real users overflowed the smaller one under concurrent load.

## Make the database do the race-safe thing

`get`, mutate, upsert loses appends when two uploads race. So appending a file id is an atomic SQL statement that inserts the row if missing and does `json_insert` with a not-exists guard in one go. It dedupes and appends in a single statement, and `save()` never touches the list it can't clobber a concurrent append.

Session expiry is one indexed `DELETE ... WHERE updated_at < cutoff` instead of a loop over rows. That works because `updated_at` is always written as UTC ISO-8601, so lexical order is chronological order.

## Thin handlers, thick behaviour

The PTB-facing bot class stays thin. Command handlers live in one mixin, message and receipt and password input in another, the shared wiring in a base. The queue worker is a standalone `JobProcessor` whose release-discipline you can read in isolation. The receipt service split into a package with `types`, `retry`, `pipeline`, and `run`.

Pure decision logic sits in its own module with no Telegram import, so it's testable without a bot. We looked at the plan's suggestion of separate command, handler, processing, and batch modules and folded them into this mixin and package structure instead, because the pipeline is single-module and these kept it cohesive without changing behaviour.

## Write down the concurrency model

Concurrency bugs are the expensive ones: double polling, unbounded parallelism, lost appends, cross-currency sums. The README and the queue stats document the intended model so nobody adds a third dial as a helpful improvement.

The health endpoint even estimates the line wait from observed mean batch time over in-flight and queued jobs divided by workers. It's a rough number, but it's honest.

## The password is a door, not a vault

One shared password, checked with a constant-time compare. The message is deleted from the chat the moment it's read, so it doesn't sit in history. Wrong attempts trigger a per-user lockout that lives in memory, because a lockout doesn't need to survive a restart.

This is a shared secret carried through chat and stored plaintext in the environment. It's a door on a trusted house, not a vault. That's the honest trade, and we say so in the README.
