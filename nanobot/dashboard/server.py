"""Starlette-based dashboard server with WebSocket event streaming.

Subscribes to the nanobot EventEmitter and forwards events as JSON to
all connected browser clients. Also serves REST endpoints for initial
state loading and the built React dashboard as static files.

Usage::

    app = create_app(emitter, store, bus, config)
    uvicorn.run(app, host="0.0.0.0", port=18791)
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.staticfiles import StaticFiles
from starlette.websockets import WebSocket, WebSocketDisconnect

if TYPE_CHECKING:
    from nanobot.bus.queue import MessageBus
    from nanobot.config.schema import Config
    from nanobot.events.emitter import EventEmitter
    from nanobot.events.models import Event
    from nanobot.events.store import EventStore


# ── WebSocket connection manager ───────────────────────────


class ConnectionManager:
    """Track active WebSocket connections and broadcast events."""

    def __init__(self) -> None:
        self._connections: list[WebSocket] = []

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._connections.append(ws)
        logger.info("Dashboard: client connected ({} total)", len(self._connections))

    def disconnect(self, ws: WebSocket) -> None:
        self._connections = [c for c in self._connections if c is not ws]
        logger.info("Dashboard: client disconnected ({} remaining)", len(self._connections))

    async def broadcast(self, data: dict) -> None:
        """Send JSON data to all connected clients."""
        if not self._connections:
            return
        text = json.dumps(data, default=str)
        dead: list[WebSocket] = []
        for ws in self._connections:
            try:
                await ws.send_text(text)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

    @property
    def count(self) -> int:
        return len(self._connections)


# ── Serializers ────────────────────────────────────────────


def _serialize_event(event: Event) -> dict:
    """Convert an Event to a JSON-safe dict."""
    return {
        "id": event.id,
        "timestamp": event.timestamp.isoformat() if event.timestamp else None,
        "event_type": event.event_type.value if hasattr(event.event_type, "value") else str(event.event_type),
        "agent_id": event.agent_id,
        "chain_id": event.chain_id,
        "payload": event.payload,
    }


def _serialize_agent_state(state) -> dict:
    return {
        "agent_id": state.agent_id,
        "status": state.status,
        "started_at": state.started_at.isoformat() if state.started_at else None,
        "completed_at": state.completed_at.isoformat() if state.completed_at else None,
        "chain_id": state.chain_id,
        "iteration": state.iteration,
        "total_tokens": state.total_tokens,
        "total_cost_usd": state.total_cost_usd,
    }


def _serialize_chain_state(state) -> dict:
    return {
        "chain_id": state.chain_id,
        "status": state.status,
        "started_at": state.started_at.isoformat() if state.started_at else None,
        "completed_at": state.completed_at.isoformat() if state.completed_at else None,
    }


def _serialize_heartbeat(state) -> dict | None:
    if state is None:
        return None
    return {
        "action": state.action,
        "checked_at": state.checked_at.isoformat() if state.checked_at else None,
        "had_content": state.had_content,
        "error": state.error,
    }


def _serialize_cron_job(state) -> dict:
    return {
        "job_id": state.job_id,
        "job_name": state.job_name,
        "last_triggered_at": state.last_triggered_at.isoformat() if state.last_triggered_at else None,
        "last_status": state.last_status,
        "last_error": state.last_error,
    }


def _serialize_task(state) -> dict:
    return {
        "task_id": state.task_id,
        "title": state.title,
        "agent_id": state.agent_id,
        "chain_id": state.chain_id,
        "status": state.status,
        "started_at": state.started_at.isoformat() if state.started_at else None,
        "completed_at": state.completed_at.isoformat() if state.completed_at else None,
    }


# ── App factory ────────────────────────────────────────────


def create_app(
    emitter: EventEmitter,
    store: EventStore,
    bus: MessageBus | None = None,
    config: Config | None = None,
    demo: bool = False,
) -> Starlette:
    """Create the Starlette dashboard app.

    Args:
        emitter: EventEmitter to subscribe for live events.
        store: EventStore for queries and derived state.
        bus: Optional MessageBus for sending commands.
        config: Optional Config for agent/team info.
        demo: If True, inject mock data on startup for preview.
    """
    manager = ConnectionManager()

    # ── Event listener → broadcast to WebSocket clients ──

    async def _on_event(event: Event) -> None:
        await manager.broadcast({
            "type": "event",
            "data": _serialize_event(event),
        })

    emitter.on("*", _on_event)

    # ── WebSocket endpoint ──

    async def ws_endpoint(ws: WebSocket) -> None:
        await manager.connect(ws)

        # Send initial snapshot
        try:
            snapshot = {
                "type": "snapshot",
                "data": {
                    "agents": {
                        k: _serialize_agent_state(v)
                        for k, v in store.active_agents.items()
                    },
                    "chains": {
                        k: _serialize_chain_state(v)
                        for k, v in store.active_chains.items()
                    },
                    "heartbeat": _serialize_heartbeat(store.last_heartbeat),
                    "cron_jobs": {
                        k: _serialize_cron_job(v)
                        for k, v in store.cron_jobs.items()
                    },
                    "task_board": {
                        k: _serialize_task(v)
                        for k, v in store.task_board.items()
                    },
                    "recent_events": [
                        _serialize_event(e)
                        for e in store.query(limit=50)
                    ],
                },
            }
            await ws.send_text(json.dumps(snapshot, default=str))
        except Exception as exc:
            logger.error("Dashboard: failed to send snapshot: {}", exc)

        # Keep connection alive, handle incoming commands
        try:
            while True:
                text = await ws.receive_text()
                try:
                    msg = json.loads(text)
                    if msg.get("type") == "command" and bus:
                        await _handle_command(msg.get("text", ""), bus)
                except json.JSONDecodeError:
                    pass
        except WebSocketDisconnect:
            pass
        finally:
            manager.disconnect(ws)

    # ── Command handler ──

    async def _handle_command(text: str, bus: MessageBus) -> None:
        """Process a command from the dashboard command bar."""
        from nanobot.bus.events import InboundMessage

        if not text.strip():
            return

        await bus.publish_inbound(InboundMessage(
            channel="dashboard",
            sender_id="dashboard-user",
            chat_id="dashboard",
            content=text.strip(),
        ))
        logger.info("Dashboard: command published → {}", text.strip()[:80])

    # ── REST endpoints ──

    async def api_state(request: Request) -> JSONResponse:
        """Current derived state snapshot."""
        return JSONResponse({
            "agents": {
                k: _serialize_agent_state(v)
                for k, v in store.active_agents.items()
            },
            "chains": {
                k: _serialize_chain_state(v)
                for k, v in store.active_chains.items()
            },
            "heartbeat": _serialize_heartbeat(store.last_heartbeat),
            "cron_jobs": {
                k: _serialize_cron_job(v)
                for k, v in store.cron_jobs.items()
            },
            "task_board": {
                k: _serialize_task(v)
                for k, v in store.task_board.items()
            },
        })

    async def api_events(request: Request) -> JSONResponse:
        """Query recent events with optional filters."""
        from nanobot.events.models import EventType

        limit = int(request.query_params.get("limit", "100"))
        event_type_str = request.query_params.get("type")
        agent_id = request.query_params.get("agent")
        chain_id = request.query_params.get("chain")

        event_type = None
        if event_type_str:
            try:
                event_type = EventType(event_type_str)
            except ValueError:
                pass

        events = store.query(
            event_type=event_type,
            agent_id=agent_id or None,
            chain_id=chain_id or None,
            limit=min(limit, 500),
        )
        return JSONResponse([_serialize_event(e) for e in events])

    async def api_config(request: Request) -> JSONResponse:
        """Basic config info for the dashboard."""
        if config is None:
            return JSONResponse({"agents": {}, "teams": {}})

        agents_info = {}
        for name, acfg in config.agents.agents.items():
            agents_info[name] = {
                "model": acfg.model,
                "system_prompt": (acfg.system_prompt or "")[:100],
            }

        teams_info = {}
        for name, tcfg in config.agents.teams.items():
            teams_info[name] = {
                "agents": tcfg.agents,
                "mode": tcfg.mode,
                "approval": tcfg.approval,
            }

        return JSONResponse({
            "default_model": config.agents.defaults.model,
            "agents": agents_info,
            "teams": teams_info,
        })

    async def api_command(request: Request) -> JSONResponse:
        """Accept a command via POST."""
        if bus is None:
            return JSONResponse({"error": "No message bus available"}, status_code=503)

        body = await request.json()
        text = body.get("text", "")
        await _handle_command(text, bus)
        return JSONResponse({"ok": True, "text": text})

    # ── Build routes ──

    routes: list = [
        WebSocketRoute("/ws", ws_endpoint),
        Route("/api/state", api_state),
        Route("/api/events", api_events),
        Route("/api/config", api_config),
        Route("/api/command", api_command, methods=["POST"]),
    ]

    # Serve built React app if it exists
    static_dir = Path(__file__).parent.parent.parent / "dashboard" / "dist"
    if static_dir.exists():
        routes.append(Mount("/", app=StaticFiles(directory=str(static_dir), html=True)))

    app = Starlette(
        routes=routes,
        middleware=[
            Middleware(
                CORSMiddleware,
                allow_origins=["*"],
                allow_methods=["*"],
                allow_headers=["*"],
            ),
        ],
    )

    # Demo mode: inject mock data on startup (runs inside uvicorn's event loop)
    if demo:
        @app.on_event("startup")
        async def _inject_demo():
            from nanobot.dashboard.demo import inject_demo_data
            await inject_demo_data(emitter)
            logger.info("Dashboard: demo data injected")

    # Attach manager for testing
    app.state.ws_manager = manager

    return app
