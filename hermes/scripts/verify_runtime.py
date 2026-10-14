"""Read-only compatibility probe for the pinned Hermes runtime."""

from __future__ import annotations

import os
import sys
from pathlib import Path


class ProbeContext:
    def __init__(self) -> None:
        self.tools = []

    def register_tool(self, **kwargs) -> None:
        self.tools.append(kwargs)


def main() -> int:
    plugin_parent = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(plugin_parent))

    import kumi
    from kumi import tools

    context = ProbeContext()
    kumi.register(context)
    names = {registered["name"] for registered in context.tools}
    expected = {
        "list_family_members",
        "create_family_task",
        "list_family_tasks",
        "assign_family_task",
        "claim_family_task",
        "complete_family_task",
    }
    if names != expected:
        raise RuntimeError(f"Registered tool mismatch: {sorted(names)}")

    os.environ.update({
        "HERMES_SESSION_PLATFORM": "line",
        "HERMES_SESSION_CHAT_ID": "runtime-probe-chat",
        "HERMES_SESSION_CHAT_TYPE": "dm",
        "HERMES_SESSION_USER_ID": "runtime-probe-user",
        "HERMES_SESSION_MESSAGE_ID": "runtime-probe-message",
    })
    source = tools._source()
    if source["p_chat_type"] != "private" or source["message_id"] != "runtime-probe-message":
        raise RuntimeError(f"Unexpected private source: {source}")

    os.environ["HERMES_SESSION_CHAT_TYPE"] = "group"
    group_source = tools._source()
    if group_source["p_chat_type"] != "group":
        raise RuntimeError(f"Unexpected group source: {group_source}")

    print(f"Kumi runtime probe passed: {len(names)} tools; private/group session context available")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
