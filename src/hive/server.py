from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from hive.manager import SessionManager
from hive.pty_bridge import PtyBridge
from hive.routes import build_router
from hive.session import SessionState
from hive.watcher import StateWatcher

log = logging.getLogger(__name__)

JSONL_DISCOVERY_TIMEOUT = 30.0
JSONL_DISCOVERY_POLL = 0.5
REAPER_INTERVAL = 1.0


def _encode_project_path(p: Path) -> str:
    return str(p).replace("/", "-")


def _bind(s, manager, watcher, jsonl_path):
    """Bind a session to a discovered JSONL file."""
    s.jsonl_path = jsonl_path
    s.claude_session_id = jsonl_path.stem
    manager.save_state()
    watcher.bind(s.id, jsonl_path)


async def _discover_and_bind_jsonl(
    manager: SessionManager,
    watcher: StateWatcher,
    bridge: PtyBridge,
    session_id: str,
) -> None:
    s = manager.get(session_id)
    if s is None:
        return
    bridge.register_pty(session_id, s.pty_master_fd)

    spawn_time = s.created_at.timestamp()
    encoded_dir = manager.claude_projects_dir / _encode_project_path(s.project_path)
    deadline = asyncio.get_event_loop().time() + JSONL_DISCOVERY_TIMEOUT
    fallback_after = asyncio.get_event_loop().time() + 5.0

    while asyncio.get_event_loop().time() < deadline:
        if encoded_dir.exists():
            all_jsonls = sorted(
                encoded_dir.glob("*.jsonl"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            # Phase 1: prefer a JSONL created/modified at or after spawn
            fresh = [p for p in all_jsonls if p.stat().st_mtime >= spawn_time - 1]
            if fresh:
                _bind(s, manager, watcher, fresh[0])
                return
            # Phase 2: after a few seconds with no fresh file, accept the
            # most-recent existing JSONL — claude likely resumed it.
            if all_jsonls and asyncio.get_event_loop().time() >= fallback_after:
                _bind(s, manager, watcher, all_jsonls[0])
                return
        await asyncio.sleep(JSONL_DISCOVERY_POLL)

    log.error("session %s: jsonl never appeared", session_id)
    s = manager.get(session_id)
    if s is not None and s.state != SessionState.DEAD:
        s.state = SessionState.DEAD
        s.pid = None
        if s.pty_master_fd is not None:
            try:
                os.close(s.pty_master_fd)
            except OSError:
                pass
            s.pty_master_fd = None
        bridge.unregister_pty(session_id)
        await watcher.emit(session_id, SessionState.DEAD)


async def _reap_dead_sessions(
    manager: SessionManager,
    watcher: StateWatcher,
    bridge: PtyBridge,
) -> None:
    """Periodically check spawned processes; mark crashed ones DEAD."""
    while True:
        await asyncio.sleep(REAPER_INTERVAL)
        for s in list(manager.list()):
            if s.pid is None or s.state == SessionState.DEAD:
                continue
            # Reap zombie if any
            try:
                pid, status = os.waitpid(s.pid, os.WNOHANG)
                if pid != 0:
                    # Process exited
                    s.state = SessionState.DEAD
                    s.pid = None
                    if s.pty_master_fd is not None:
                        try:
                            os.close(s.pty_master_fd)
                        except OSError:
                            pass
                        s.pty_master_fd = None
                    bridge.unregister_pty(s.id)
                    watcher.unbind(s.id)
                    await watcher.emit(s.id, SessionState.DEAD)
            except (ProcessLookupError, ChildProcessError):
                # Process doesn't exist or already reaped
                s.state = SessionState.DEAD
                s.pid = None
                if s.pty_master_fd is not None:
                    try:
                        os.close(s.pty_master_fd)
                    except OSError:
                        pass
                    s.pty_master_fd = None
                bridge.unregister_pty(s.id)
                watcher.unbind(s.id)
                await watcher.emit(s.id, SessionState.DEAD)


async def _sync_session_states(
    manager: SessionManager,
    watcher: StateWatcher,
) -> None:
    """Subscribe to watcher events and update session objects."""
    queue = watcher.subscribe()
    while True:
        sid, state = await queue.get()
        s = manager.get(sid)
        if s is not None:
            s.state = state


def build_app(
    claude_command: list[str] | None = None,
    claude_projects_dir: Path | None = None,
    state_path: Path | None = None,
    static_dir: Path | None = None,
) -> FastAPI:
    manager = SessionManager(
        claude_command=claude_command,
        claude_projects_dir=claude_projects_dir,
        state_path=state_path,
    )
    watcher = StateWatcher(poll_interval=0.5)
    bridge = PtyBridge()
    discovery_started: set[str] = set()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        watcher.start()
        manager.restart_persisted()
        for s in manager.list():
            discovery_started.add(s.id)
            asyncio.create_task(
                _discover_and_bind_jsonl(manager, watcher, bridge, s.id)
            )
        reaper_task = asyncio.create_task(
            _reap_dead_sessions(manager, watcher, bridge)
        )
        sync_task = asyncio.create_task(
            _sync_session_states(manager, watcher)
        )
        yield
        reaper_task.cancel()
        sync_task.cancel()
        for s in manager.list():
            manager.kill(s.id)
        watcher.stop()

    app = FastAPI(lifespan=lifespan)
    app.state.manager = manager
    app.state.watcher = watcher
    app.state.bridge = bridge
    app.include_router(build_router(manager, watcher))

    @app.middleware("http")
    async def post_spawn_discovery(request, call_next):
        response = await call_next(request)
        for s in manager.list():
            if (
                s.jsonl_path is None
                and s.pty_master_fd is not None
                and s.id not in discovery_started
            ):
                discovery_started.add(s.id)
                asyncio.create_task(
                    _discover_and_bind_jsonl(manager, watcher, bridge, s.id)
                )
        return response

    @app.websocket("/ws/state")
    async def ws_state(ws: WebSocket):
        await ws.accept()
        queue = watcher.subscribe()
        # Send current state for all known sessions on connect
        for s in manager.list():
            await ws.send_json({"session_id": s.id, "state": s.state.value})
        try:
            while True:
                sid, state = await queue.get()
                await ws.send_json({"session_id": sid, "state": state.value})
        except WebSocketDisconnect:
            return

    @app.websocket("/ws/pty/{session_id}")
    async def ws_pty(ws: WebSocket, session_id: str):
        await ws.accept()
        # Adapter so PtyBridge.attach gets the receive_bytes/send_bytes API
        class _Adapter:
            async def receive_bytes(self):
                return await ws.receive_bytes()
            async def send_bytes(self, data: bytes):
                await ws.send_bytes(data)
            async def close(self):
                await ws.close()
        try:
            await bridge.attach(session_id, _Adapter())
        except WebSocketDisconnect:
            return

    if static_dir is not None and static_dir.is_dir():
        app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")

    return app
