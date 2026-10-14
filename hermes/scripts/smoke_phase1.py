"""Smoke-test Supabase Auth and the read-only Kumi RPC from the Hermes host."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def read_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def parse_ids(value: str) -> list[str]:
    return [item.strip() for item in value.replace(";", ",").split(",") if item.strip()]


def main() -> int:
    hermes_home = Path.home() / ".hermes"
    for key, value in read_dotenv(Path.home() / ".config" / "kumi" / "hermes.env").items():
        os.environ[key] = value
    line_env = read_dotenv(hermes_home / ".env")
    users = parse_ids(line_env.get("LINE_ALLOWED_USERS", ""))
    if not users:
        raise RuntimeError("LINE user allowlist is empty")

    sys.path.insert(0, str(hermes_home / "plugins"))
    from kumi.client import KumiConfig, SupabaseRpcClient  # noqa: PLC0415
    from kumi.tools import handle_list_family_tasks  # noqa: PLC0415
    from gateway.session_context import clear_session_vars, set_session_vars  # noqa: PLC0415

    config = KumiConfig.from_env()
    client = SupabaseRpcClient(config)
    try:
        body = client.rpc(
            "list_family_members",
            {
                "p_provider": "line",
                "p_provider_user_id": users[0],
                "p_chat_type": "private",
                "p_chat_id": users[0],
            },
        )
    finally:
        client.close()

    members = body.get("data") if isinstance(body.get("data"), list) else []
    tokens = set_session_vars(
        platform="line",
        chat_id=users[0],
        chat_type="dm",
        user_id=users[0],
        message_id="kumi-phase1-operator-smoke",
    )
    try:
        task_body = json.loads(handle_list_family_tasks({
            "scope": "mine",
            "status": "open",
            "limit": 50,
        }))
    finally:
        clear_session_vars(tokens)
    tasks = task_body.get("data") if isinstance(task_body.get("data"), list) else []
    print(json.dumps({
        "ok": body.get("ok") is True,
        "member_count": len(members),
        "member_names": [member.get("name") for member in members],
        "trusted_context_task_read_ok": task_body.get("ok") is True,
        "open_task_count": len(tasks),
        "mutations_enabled": config.mutations_enabled,
    }, ensure_ascii=False))
    return 0 if body.get("ok") is True and task_body.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
