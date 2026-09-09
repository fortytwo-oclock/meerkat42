"""Backwards-compatible entrypoint.

The hub is now a package (`hub.main:app`). This shim keeps `uvicorn server:app`
and older deploy scripts working.
"""
from hub.main import app  # noqa: F401
