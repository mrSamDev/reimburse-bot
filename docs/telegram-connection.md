# How the bot connects to Telegram

**Telegram never connects to the bot. The bot connects to Telegram.**

This bot uses **long polling**, not a webhook. The distinction decides whether you
need a public URL (and ngrok) — you don't.

## The model

Think of your phone: it dials *out* to WhatsApp's servers; WhatsApp never dials
*into* your phone. Same here.

```
Laptop bot ──outbound──> api.telegram.org   (port 443)
    "Any updates for me?"
    <-- "no"
    "Any now?"
    <-- "user sent /start"     -> bot replies
    "Any now?"
    <-- "user sent a photo"    -> bot stores the file_id
```

- The bot keeps asking Telegram "any updates?" — that loop is the *poll*
  (`application.run_polling(...)`).
- Telegram Cloud stores the messages and receipt photos in the meantime.
- The bot only downloads a receipt photo when `/generate` + password succeeds,
  pulling it back from Telegram Cloud.

## What your machine needs

Only **outbound internet** to reach `api.telegram.org:443`. Because the
connection is outbound:

- No public IP or domain required.
- No port forwarding / NAT / router config.
- No ngrok for inbound traffic.
- Works on a laptop "in your lap" on normal wifi.

## The one trade-off

A long-polling bot must stay **running and online** to respond.

- Laptop asleep / Wi-Fi off → bot offline → no replies.
- The in-memory session (staged `file_id`s) is lost on restart.

This is a deliberate trade-off for V1. The plan (§31, §45) explicitly selects
long polling and excludes webhooks, Redis, VPS hosting and any persistent
database. It is also why receipts stay inside Telegram — the bot needs no
permanent server storage.

## How the receipts flow works

1. User uploads a photo → Telegram stores it, bot receives only the `file_id`.
2. Bot saves the `file_id` in the in-memory session (no image on disk).
3. `/generate` + correct password → bot pulls the file back from Telegram Cloud,
   runs the vision AI, builds the PDF, sends it back, then deletes all temp files.

See `app/main.py` (`run_polling`) for the wiring.
