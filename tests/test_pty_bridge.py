import asyncio
import os
import pty

import pytest

from hive.pty_bridge import PtyBridge


class FakeWebSocket:
    """Minimal WebSocket stand-in for testing bridge behavior."""
    def __init__(self):
        self.outbox: list[bytes] = []
        self.inbox: asyncio.Queue = asyncio.Queue()
        self._closed = False

    async def receive_bytes(self) -> bytes:
        item = await self.inbox.get()
        if item is None:
            raise RuntimeError("closed")
        return item

    async def send_bytes(self, data: bytes) -> None:
        self.outbox.append(data)

    async def close(self) -> None:
        self._closed = True
        await self.inbox.put(None)


@pytest.mark.asyncio
async def test_pty_to_socket(tmp_path):
    master, slave = pty.openpty()
    bridge = PtyBridge(scrollback_bytes=1024)
    bridge.register_pty("sess-1", master)

    ws = FakeWebSocket()
    task = asyncio.create_task(bridge.attach("sess-1", ws))

    # Write something into the slave end => bridge should see it on master
    os.write(slave, b"hello from pty\n")

    # Wait briefly for bytes to arrive
    deadline = asyncio.get_event_loop().time() + 2
    while not ws.outbox and asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(0.05)
    assert any(b"hello from pty" in chunk for chunk in ws.outbox)

    await ws.close()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    os.close(slave)
    bridge.unregister_pty("sess-1")


@pytest.mark.asyncio
async def test_socket_to_pty():
    master, slave = pty.openpty()
    bridge = PtyBridge(scrollback_bytes=1024)
    bridge.register_pty("sess-2", master)

    ws = FakeWebSocket()
    task = asyncio.create_task(bridge.attach("sess-2", ws))

    await ws.inbox.put(b"input from browser\n")

    # Read from slave end to verify the bridge wrote to master
    await asyncio.sleep(0.1)
    data = os.read(slave, 1024)
    assert b"input from browser" in data

    await ws.close()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    os.close(slave)
    bridge.unregister_pty("sess-2")


@pytest.mark.asyncio
async def test_scrollback_replayed_on_attach():
    master, slave = pty.openpty()
    bridge = PtyBridge(scrollback_bytes=1024)
    bridge.register_pty("sess-3", master)

    # Write before any viewer attaches - should land in scrollback
    os.write(slave, b"backlog content\n")
    await asyncio.sleep(0.2)  # let bridge's reader drain it

    ws = FakeWebSocket()
    task = asyncio.create_task(bridge.attach("sess-3", ws))
    await asyncio.sleep(0.1)
    assert any(b"backlog content" in chunk for chunk in ws.outbox)

    await ws.close()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    os.close(slave)
    bridge.unregister_pty("sess-3")
