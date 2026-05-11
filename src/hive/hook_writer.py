from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

from hive.hook_state import remove_session_state, state_dir, state_file_path

EVENT_TO_STATE = {
    "UserPromptSubmit": "working",
    "Stop": "waiting",
    "SubagentStop": "waiting",
    "Notification": "waiting",
    "SessionStart": "waiting",
}


def main() -> int:
    session_name = os.environ.get("HIVE_SESSION")
    if not session_name:
        return 0

    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0
    if not isinstance(payload, dict):
        return 0

    event = payload.get("hook_event_name", "")

    if event == "SessionEnd":
        remove_session_state(session_name)
        return 0

    state = EVENT_TO_STATE.get(event)
    if state is None:
        return 0

    record = {
        "state": state,
        "session_id": payload.get("session_id", ""),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "event": event,
    }

    state_dir().mkdir(parents=True, exist_ok=True)
    path = state_file_path(session_name)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(record))
    os.replace(tmp, path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
