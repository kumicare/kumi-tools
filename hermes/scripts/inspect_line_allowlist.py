"""Print masked LINE allowlist identities for operator mapping.

Never prints the channel token or a complete LINE user ID.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx


def read_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def main() -> int:
    env = read_dotenv(Path.home() / ".hermes" / ".env")
    token = env.get("LINE_CHANNEL_ACCESS_TOKEN", "")
    raw_users = env.get("LINE_ALLOWED_USERS", "")
    user_ids = [item.strip() for item in raw_users.replace(";", ",").split(",") if item.strip()]
    if not token or not user_ids:
        raise RuntimeError("LINE token or user allowlist is unavailable")

    rows = []
    with httpx.Client(timeout=10.0) as client:
        for index, user_id in enumerate(user_ids, start=1):
            response = client.get(
                f"https://api.line.me/v2/bot/profile/{user_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            display_name = None
            if response.status_code == 200:
                display_name = response.json().get("displayName")
            rows.append({
                "index": index,
                "masked_user_id": f"…{user_id[-4:]}",
                "display_name": display_name,
                "profile_status": response.status_code,
            })
    print(json.dumps(rows, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
