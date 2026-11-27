"""TEMPORARY verification endpoint: /api/db_counts

Reads row counts from the production PostgreSQL database configured via the
DATABASE_URL environment variable and returns them as JSON.

SECURITY: this endpoint never reads, prints, or returns the DATABASE_URL value
or any other secret. On failure it returns only the exception type name.
"""

from fastapi import FastAPI
from fastapi.responses import JSONResponse

import psycopg

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

COUNT_TABLES = [
    "users", "media", "votes", "owner_votes", "profile_votes", "roles",
    "profile_photos", "group_chats", "group_counts", "review_messages",
    "bot_settings", "moderation_audit", "conversation_state", "rate_limits",
]


@app.get("/")
def db_counts():
    dsn = __import__("os").environ.get("DATABASE_URL", "")
    if not dsn:
        return JSONResponse({"ok": False, "error": "DATABASE_URL not set"}, status_code=500)
    try:
        with psycopg.connect(dsn, connect_timeout=10) as conn:
            counts = {}
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public'"
                )
                existing = {r[0] for r in cur.fetchall()}
                for table in COUNT_TABLES:
                    if table in existing:
                        cur.execute(f'SELECT COUNT(*) FROM "{table}"')
                        counts[table] = cur.fetchone()[0]
                    else:
                        counts[table] = "MISSING_TABLE"
            return JSONResponse({"ok": True, "counts": counts})
    except Exception as exc:  # never leak the DSN or credentials
        return JSONResponse(
            {"ok": False, "error": type(exc).__name__}, status_code=500
        )
