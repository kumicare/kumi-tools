"""Model-level routing probe for LINE Google Calendar requests.

The final agent response is captured but never printed because it can contain
private calendar data. Only routing booleans are emitted.
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime
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


def main() -> int:
    home = Path.home()
    hermes_home = home / ".hermes"
    config_dir = home / ".config" / "kumi"
    child_env = dict(os.environ)
    for env_file in (
        hermes_home / ".env",
        config_dir / "hermes.env",
        config_dir / "google.env",
    ):
        child_env.update(read_dotenv(env_file))

    users = [
        item.strip()
        for item in child_env.get("LINE_ALLOWED_USERS", "").replace(";", ",").split(",")
        if item.strip()
    ]
    if not users:
        raise RuntimeError("At least one allowed LINE user is required")

    child_env.update({
        "HERMES_SESSION_PLATFORM": "line",
        "HERMES_SESSION_CHAT_ID": users[0],
        "HERMES_SESSION_CHAT_TYPE": "dm",
        "HERMES_SESSION_USER_ID": users[0],
        "HERMES_SESSION_MESSAGE_ID": (
            "kumi-routing-probe-" + datetime.now().strftime("%Y%m%dT%H%M%S%f")
        ),
    })
    hermes_bin = hermes_home / "hermes-agent" / "venv" / "bin" / "hermes"
    completed = subprocess.run(
        [
            str(hermes_bin),
            "-t",
            "kumi",
            "-z",
            "請查詢我未來七天的行程。這是 LINE 使用者的驗證請求。",
        ],
        cwd=hermes_home / "hermes-agent",
        env=child_env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    response = completed.stdout.strip()
    combined = f"{completed.stdout}\n{completed.stderr}"
    auth_failure_markers = (
        "NOT_AUTHENTICATED",
        "No token at",
        "OAuth token",
        "完成一次 Google Workspace 授權",
    )
    no_oauth_failure = not any(marker in combined for marker in auth_failure_markers)
    calendar_response = any(
        marker in response for marker in ("行程", "活動", "日曆", "Calendar")
    )
    ok = completed.returncode == 0 and bool(response) and no_oauth_failure and calendar_response
    print(json.dumps({
        "ok": ok,
        "process_ok": completed.returncode == 0,
        "response_present": bool(response),
        "calendar_response": calendar_response,
        "oauth_failure_absent": no_oauth_failure,
    }, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
