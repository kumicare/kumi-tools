"""Read-only Google Workspace smoke test that never prints family data."""

from __future__ import annotations

import json
import os
import sys
from datetime import date, timedelta
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
    config_dir = Path.home() / ".config" / "kumi"
    for key, value in read_dotenv(config_dir / "hermes.env").items():
        os.environ[key] = value
    google_env = config_dir / "google.env"
    if google_env.exists():
        for key, value in read_dotenv(google_env).items():
            os.environ[key] = value

    hermes_home = Path.home() / ".hermes"
    line_env = read_dotenv(hermes_home / ".env")
    users = parse_ids(line_env.get("LINE_ALLOWED_USERS", ""))
    if len(users) < 2:
        raise RuntimeError("Two LINE users are required for the family acceptance probe")
    sys.path.insert(0, str(hermes_home / "plugins"))
    from kumi.google_workspace import GoogleWorkspaceClient, GoogleWorkspaceConfig  # noqa: PLC0415
    from kumi.tools import handle_list_family_calendar_events, handle_read_family_sheet  # noqa: PLC0415
    from gateway.session_context import clear_session_vars, set_session_vars  # noqa: PLC0415

    config = GoogleWorkspaceConfig.from_env()
    client = GoogleWorkspaceClient(config)
    try:
        sheet_counts = {}
        for sheet in config.allowed_sheets:
            body = client.read_sheet(sheet, max_rows=200)
            sheet_counts[sheet] = body["data"]["row_count"]
        today = date.today()
        events = client.list_calendar_events(today, today + timedelta(days=31), max_results=100)
    finally:
        client.close()

    actor_results = []
    for index, user_id in enumerate(users[:2], start=1):
        tokens = set_session_vars(
            platform="line",
            chat_id=user_id,
            chat_type="dm",
            user_id=user_id,
            message_id=f"kumi-google-actor-{index}-read-smoke",
        )
        try:
            sheet_result = json.loads(handle_read_family_sheet({
                "sheet": config.allowed_sheets[0],
                "max_rows": 10,
            }))
            calendar_result = json.loads(handle_list_family_calendar_events({
                "start_date": today.isoformat(),
                "end_date": (today + timedelta(days=31)).isoformat(),
                "max_results": 100,
            }))
        finally:
            clear_session_vars(tokens)
        sheet_rows = ((sheet_result.get("data") or {}).get("rows") or [])
        calendar_events = calendar_result.get("data") or []
        actor_results.append({
            "actor": f"allowed_line_actor_{index}",
            "sheet_read_ok": sheet_result.get("ok") is True,
            "sheet_row_count": len(sheet_rows),
            "calendar_read_ok": calendar_result.get("ok") is True,
            "calendar_event_count": len(calendar_events),
        })

    print(json.dumps({
        "ok": True,
        "allowed_sheet_row_counts": sheet_counts,
        "next_31_days_event_count": len(events["data"]),
        "timezone": config.timezone,
        "mutations_enabled": config.mutations_enabled,
        "trusted_line_actor_results": actor_results,
    }, ensure_ascii=False))
    return 0 if all(
        item["sheet_read_ok"] and item["calendar_read_ok"] for item in actor_results
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
