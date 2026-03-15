"""Dashboard server for nanobot — Starlette + WebSocket."""

from nanobot.dashboard.server import create_app

__all__ = ["create_app"]
