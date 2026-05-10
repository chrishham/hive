from __future__ import annotations

import asyncio
import os
from collections import deque


class _PtyState:
    def __init__(self, fd: int, scrollback_bytes: int):
        self.fd = fd
        self.scrollback = deque()
        self.scrollback_size = 0
        self.scrollback_max = scrollback_bytes
        self.viewers: set = set()  # set of asyncio.Queue


class PtyBridge:
    def __init__(self, scrollback_bytes: int = 256 * 1024):
        self.scrollback_bytes = scrollback_bytes
        self._ptys: dict[str, _PtyState] = {}

    def register_pty(self, session_id: str, master_fd: int) -> None:
        state = _PtyState(master_fd, self.scrollback_bytes)
        self._ptys[session_id] = state
        loop = asyncio.get_event_loop()
        loop.add_reader(master_fd, self._on_pty_readable, session_id)

    def unregister_pty(self, session_id: str) -> None:
        state = self._ptys.pop(session_id, None)
        if state is None:
            return
        loop = asyncio.get_event_loop()
        try:
            loop.remove_reader(state.fd)
        except (ValueError, OSError):
            pass

    def _on_pty_readable(self, session_id: str) -> None:
        state = self._ptys.get(session_id)
        if state is None:
            return
        try:
            data = os.read(state.fd, 4096)
        except OSError:
            return
        if not data:
            return
        # Append to scrollback (trim to max)
        state.scrollback.append(data)
        state.scrollback_size += len(data)
        while state.scrollback_size > state.scrollback_max and state.scrollback:
            old = state.scrollback.popleft()
            state.scrollback_size -= len(old)
        # Fan out to viewers
        for q in list(state.viewers):
            try:
                q.put_nowait(data)
            except asyncio.QueueFull:
                pass

    async def attach(self, session_id: str, websocket) -> None:
        state = self._ptys.get(session_id)
        if state is None:
            return

        viewer_q: asyncio.Queue = asyncio.Queue(maxsize=1024)
        state.viewers.add(viewer_q)

        # Replay scrollback
        for chunk in list(state.scrollback):
            await websocket.send_bytes(chunk)

        async def pty_to_socket():
            try:
                while True:
                    data = await viewer_q.get()
                    await websocket.send_bytes(data)
            except Exception:
                return  # client disconnected or socket closed

        async def socket_to_pty():
            try:
                while True:
                    data = await websocket.receive_bytes()
                    if state.fd is not None:
                        try:
                            os.write(state.fd, data)
                        except OSError:
                            return
            except Exception:
                return  # client disconnected or socket closed

        out_task = asyncio.create_task(pty_to_socket())
        in_task = asyncio.create_task(socket_to_pty())
        try:
            done, pending = await asyncio.wait(
                [out_task, in_task],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for t in pending:
                t.cancel()
            # Consume exceptions on completed tasks so asyncio doesn't log
            # "Task exception was never retrieved" on clean disconnects.
            for t in done:
                if not t.cancelled():
                    t.exception()
        finally:
            state.viewers.discard(viewer_q)
