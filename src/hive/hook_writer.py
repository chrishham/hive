from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timezone

from hive.hook_state import remove_session_state, state_dir, state_file_path

EVENT_TO_STATE = {
    "UserPromptSubmit": "working",
    "Stop": "waiting",
    "Notification": "waiting",
    "SessionStart": "waiting",
}

_MAX_STDIN_BYTES = 64 * 1024
_MAX_SESSION_ID_LEN = 256


def main() -> int:
    session_name = os.environ.get("HIVE_SESSION")
    if not session_name:
        return 0
    try:
        raw = sys.stdin.buffer.read(_MAX_STDIN_BYTES + 1)
    except (AttributeError, OSError):
        try:
            raw = sys.stdin.read(_MAX_STDIN_BYTES + 1).encode("utf-8", errors="replace")
        except Exception:
            return 0
    if len(raw) > _MAX_STDIN_BYTES:
        return 0
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, ValueError, UnicodeDecodeError):
        return 0
    if not isinstance(payload, dict):
        return 0
    event = payload.get("hook_event_name", "")
    raw_session_id = payload.get("session_id", "")
    if not isinstance(raw_session_id, str):
        raw_session_id = ""
    session_id = raw_session_id[:_MAX_SESSION_ID_LEN]
    try:
        if event == "SessionEnd":
            remove_session_state(session_name)
            return 0
        state = EVENT_TO_STATE.get(event)
        if state is None:
            return 0
        record = {
            "state": state,
            "session_id": session_id,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "event": event,
        }
        path = state_file_path(session_name)
        sdir = state_dir()
        sdir.mkdir(parents=True, exist_ok=True)
        if sdir.is_symlink() or path.is_symlink():
            return 0
        fd, tmp = tempfile.mkstemp(
            dir=sdir,
            prefix=f".{session_name}.",
            suffix=".json.tmp",
        )
        try:
            with os.fdopen(fd, "w") as f:
                f.write(json.dumps(record))
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass
            raise
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
