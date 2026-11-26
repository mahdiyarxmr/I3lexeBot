"""Vercel serverless function entrypoint.

Vercel maps ``api/webhook.py`` to the HTTP route ``/api/webhook`` and serves
the ASGI application exported here. The application itself (routes, Telegram
update dispatch, secret validation) is defined in the project-root ``app.py``.

No polling is performed anywhere in the deployed runtime.
"""

from app import app  # noqa: F401  (Vercel discovers the ASGI ``app`` object)

__all__ = ["app"]
