#!/usr/bin/env python3
"""Local development launcher for the i3l.exe Telegram bot.

Historically this file contained the whole polling bot. The bot has been
migrated to a Vercel-compatible webhook runtime: all logic now lives in the
``bot/`` package and the ASGI entrypoint is ``app.py``. This file is kept as a
convenient way to run the *webhook* app locally during development.

It deliberately does **not** start a polling loop. To test locally
without a public URL, expose the local server with a tunnel (e.g. ``ngrok`` or
``vercel tunnel``) and point ``WEBHOOK_URL`` at the resulting ``/api/webhook``
address, then run ``scripts/register_webhook.py``.

Usage:
    pip install -r requirements.txt
    python main.py                 # serves http://0.0.0.0:8000
    PORT=3000 python main.py
"""

from __future__ import annotations

import os
import sys


def main() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover - optional local convenience
        load_dotenv = None
    if load_dotenv is not None:
        load_dotenv()

    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))

    import uvicorn

    print("Starting i3l.exe webhook server (no polling).")
    print(f"  ASGI app : app:app")
    print(f"  Listening: http://{host}:{port}")
    print(f"  Webhook  : http://{host}:{port}/api/webhook")
    uvicorn.run("app:app", host=host, port=port, log_level=os.environ.get("LOG_LEVEL", "info"))


if __name__ == "__main__":
    sys.exit(main())
