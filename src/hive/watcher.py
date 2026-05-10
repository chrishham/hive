from __future__ import annotations

import asyncio
import threading
from datetime import datetime, timezone
from pathlib import Path

from hive.derive import derive_state
from hive.session import SessionState


class StateWatcher:
    def __init__(self, poll_interval: float = 0.5):
        self.poll_interval = poll_interval
        self._bindings: dict[str, Path] = {}
        self._last_state: dict[str, SessionState] = {}
        self._subscribers: list[asyncio.Queue] = []
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task | None = None
        self._running = False

    def bind(self, session_id: str, jsonl_path: Path) -> None:
        with self._lock:
            self._bindings[session_id] = jsonl_path
            self._last_state.pop(session_id, None)

    def unbind(self, session_id: str) -> None:
        with self._lock:
            self._bindings.pop(session_id, None)
            self._last_state.pop(session_id, None)

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        with self._lock:
            self._subscribers.append(q)
        return q

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._loop = asyncio.get_event_loop()
        self._task = self._loop.create_task(self._poll_loop())

    def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            self._task = None

    async def _poll_loop(self) -> None:
        try:
            while self._running:
                await self._tick()
                await asyncio.sleep(self.poll_interval)
        except asyncio.CancelledError:
            pass

    async def _tick(self) -> None:
        with self._lock:
            snapshot = list(self._bindings.items())
        now = datetime.now(timezone.utc)
        for sid, path in snapshot:
            new_state = derive_state(path, now)
            old_state = self._last_state.get(sid)
            if new_state != old_state:
                self._last_state[sid] = new_state
                await self._broadcast((sid, new_state))

    async def _broadcast(self, evt: tuple[str, SessionState]) -> None:
        with self._lock:
            subs = list(self._subscribers)
        for q in subs:
            await q.put(evt)

    async def emit(self, session_id: str, state: SessionState) -> None:
        """Broadcast a state change without going through JSONL derivation.

        Used by the reaper to publish DEAD events when a process exits.
        """
        self._last_state[session_id] = state
        await self._broadcast((session_id, state))
