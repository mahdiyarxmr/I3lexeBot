# i3l.exe — Telegram Bot (Vercel Webhook Runtime)

The i3l.exe Telegram bot has been migrated from a **polling** process to a
**Vercel serverless webhook** application backed by **PostgreSQL**. Every existing
feature, message, keyboard and business rule of the original `main.py` bot is
preserved.

- Telegram updates are received via a **webhook** (`POST /api/webhook`).
- The bot is built with **FastAPI** + **python-telegram-bot**'s async
  `Application` API.
- Data is stored in **PostgreSQL** (Neon or any Vercel-compatible provider).
- Multi-step conversation state and anti-spam counters are **persistent**
  (PostgreSQL), so they survive separate serverless invocations.
- **No polling loop** and **no permanent background process** anywhere in the
  runtime.

## Project layout

| Path | Purpose |
| --- | --- |
| `api/webhook.py` | **Vercel serverless function** (route `/api/webhook`). Exports the ASGI `app`. |
| `app.py` | **ASGI entrypoint** (FastAPI). Secret validation, `Update` parsing, dispatch. |
| `bot/telegram_app.py` | Builds the python-telegram-bot `Application` and registers all handlers. |
| `bot/handlers.py` | All bot handlers (ported faithfully from the original bot). |
| `bot/db.py` | Async PostgreSQL access layer + connection pool (psycopg v3). |
| `bot/schema.py` | PostgreSQL DDL — the single source of truth for the schema. |
| `bot/state.py` | Persistent per-user conversation state (replaces `context.user_data`). |
| `bot/ratelimit.py` | Persistent anti-spam rate limiter. |
| `bot/config.py` | Environment configuration + validation. |
| `bot/ui.py` | Premium emoji + inline keyboard helpers (unchanged). |
| `bot/runtime.py` | Lazily-initialised runtime singletons. |
| `botteli3l_new.py` | **Local development launcher** (runs the webhook app with uvicorn; no polling). |
| `scripts/migrate_sqlite_to_postgres.py` | Safe SQLite → PostgreSQL import utility. |
| `scripts/register_webhook.py` | Registers/clears the Telegram webhook. |
| `requirements.txt` | Python dependencies (Vercel auto-installs). |

## Environment variables

All secrets are read from the environment. **Never commit `.env` or any secret.**

| Variable | Required | Description |
| --- | --- | --- |
| `BOT_TOKEN` | ✅ | Telegram bot token from `@BotFather`. |
| `OWNER_ID` | ✅ | Numeric owner/user id (original value: `6341879634`). |
| `DATABASE_URL` | ✅ | PostgreSQL connection string (e.g. Neon). |
| `WEBHOOK_SECRET` | ✅ | Random secret token; must match the Telegram webhook secret. |
| `WEBHOOK_URL` | ✅ (registration) | Full deployed webhook URL: `https://YOUR-DOMAIN/api/webhook`. |

Optional: `DB_POOL_MAX` (default `4`), `DB_POOL_MIN` (default `1`),
`DB_POOL_TIMEOUT` (default `10`), `PORT`/`HOST` (local dev only).

A template is provided in [`.env.example`](.env.example).

## Vercel deployment

1. Import the repository on Vercel (or connect the GitHub project so future
   pushes auto-deploy).
2. Add the environment variables above in **Project → Settings → Environment
   Variables** (production, and development if you use it).
3. Deploy. No build command is required — Vercel detects `requirements.txt`
   and installs dependencies automatically, and serves `api/webhook.py` as a
   serverless function using **native Python runtime detection** (no
   `vercel.json` runtime override is needed or present).
4. The function is served at:

   ```
   https://YOUR-VERCEL-DOMAIN/api/webhook
   ```

   Vercel recognises the ASGI `app` object exported by `api/webhook.py`
   (re-exported from `app.py`).

## Database migration (SQLite → PostgreSQL)

> ⚠️ The original SQLite files (`i3l_backup.db`, `i3l_v1.db`) are **not modified
> or deleted** by this process. The migration is idempotent and safe to re-run.

1. **Create a PostgreSQL database** in Neon (or your provider). Copy the
   connection string — this becomes `DATABASE_URL`. You do **not** need to run
   any DDL yourself: the runtime and the import script both create the schema
   automatically.
2. **Import the historical data** from the repository root:

   ```bash
   export DATABASE_URL="postgresql://user:password@ep-xxx.region.provider.neon.tech/i3lexebot?sslmode=require"
   python scripts/migrate_sqlite_to_postgres.py
   ```

   To import several databases (the second is skipped where ids already
   exist):

   ```bash
   python scripts/migrate_sqlite_to_postgres.py --sqlite i3l_backup.db --sqlite i3l_v1.db
   ```

   Preview without writing:

   ```bash
   python scripts/migrate_sqlite_to_postgres.py --dry-run
   ```

3. What the script does:
   - creates every table/column from the legacy SQLite schema (including the
     historical `owner_votes`, `profile_votes`, `roles`, `profile_photos`
     tables and the moderation tables),
   - copies `users`, `media`, `votes`, review messages, group settings,
     moderation audit and all historical records, preserving primary keys,
   - backfills `media.review_status` from `approved`,
   - resets identity sequences so new inserts do not collide,
   - adds foreign keys idempotently (a dangling legacy reference is reported
     and skipped, never fatal).

4. **Do not delete the SQLite backups.** Keep them as a rollback source.

### Notes

- `media.file_id` values (Telegram file ids) are stored verbatim — no media is
  downloaded or re-uploaded. Telegram file ids are reusable, so nothing needs
  to move to external object storage.
- The schema intentionally leaves `media.sender_id` without a foreign key:
  legacy databases contain a few media rows whose sender never ran `/start` and
  has no `users` row. This preserves the historical data exactly.

## Register the Telegram webhook

**Only after** the deployment is live and all environment variables are set:

```bash
export BOT_TOKEN="123456:..."
export OWNER_ID="6341879634"
export WEBHOOK_URL="https://YOUR-VERCEL-DOMAIN/api/webhook"
export WEBHOOK_SECRET="a-long-random-string"

# Check the configuration without calling Telegram:
python scripts/register_webhook.py --dry-run

# Register the webhook (setWebhook with the secret token):
python scripts/register_webhook.py
```

The runtime validates the `X-Telegram-Bot-Api-Secret-Token` header on every
request. `BOT_TOKEN` and `WEBHOOK_SECRET` are never logged or returned in HTTP
responses.

## Local development

```bash
pip install -r requirements.txt
cp .env.example .env          # fill in real values
python botteli3l_new.py       # serves http://0.0.0.0:8000
```

Local mode runs the **webhook** server, not polling. Expose it with a tunnel
(e.g. `ngrok http 8000`), set `WEBHOOK_URL` to the tunneled `/api/webhook`
address, and register the webhook.

## Tests / checks

```bash
python -m py_compile app.py botteli3l_new.py bot/*.py scripts/*.py api/*.py
python -m pytest tests/ -q
```

## Features preserved

`/start`, `/help`, `/profile`, `/announce`, `/manage`, `/setreviewgroup`,
`/restoremedia`; admin & VIP management; owner permissions; user profiles and
biographies; profile photos; user levels and rankings; video / photo / music
archives; media submissions and moderation; approval/rejection workflows;
voting and vote updates; group registration and tracking; automatic
promotional messages; top users and top media; inline keyboards; premium
Telegram custom emojis; anti-spam protection; and all Persian messages/UI text.
