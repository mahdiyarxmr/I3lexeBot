"""i3l.exe Telegram bot — Vercel/webhook runtime package.

This package contains the production webhook runtime. The legacy, polling-based
``main.py`` has been converted into a thin local development launcher; all bot
logic now lives here so that it can run inside Vercel serverless functions.
"""

__all__ = ["__version__"]
__version__ = "2.0.0"
