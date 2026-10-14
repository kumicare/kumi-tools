"""Production write/read/cleanup probe for both authorized LINE actors.

The probe prints only boolean/count metadata. It never prints LINE IDs, event IDs,
calendar contents, or service-account credentials.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo


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
    for env_file in (config_dir / "hermes.env", config_dir / "google.env"):
        for key, value in read_dotenv(env_file).items():
            os.environ[key] = value

    hermes_home = Path.home() / ".hermes"
    users = parse_ids(read_dotenv(hermes_home / ".env").get("LINE_ALLOWED_USERS", ""))
    if len(users) < 2:
        raise RuntimeError("Two LINE users are required for the family acceptance probe")

    sys.path.insert(0, str(hermes_home / "plugins"))
    from gateway.session_context import clear_session_vars, set_session_vars  # noqa: PLC0415
    from kumi.google_workspace import GoogleWorkspaceClient, GoogleWorkspaceConfig  # noqa: PLC0415
    from kumi.tools import (  # noqa: PLC0415
        handle_create_family_calendar_event,
        handle_list_family_calendar_events,
    )

    config = GoogleWorkspaceConfig.from_env()
    if not config.mutations_enabled:
        raise RuntimeError("Google Workspace mutations are not enabled")

    timezone = ZoneInfo(config.timezone)
    event_day = date.today() + timedelta(days=180)
    run_key = datetime.now(timezone).strftime("%Y%m%dT%H%M%S%f")
    created_ids: list[str] = []
    actor_results: list[dict[str, object]] = []
    cleanup_client = GoogleWorkspaceClient(config)
    base_url = (
        f"{cleanup_client.CALENDAR_BASE}/"
        f"{quote(config.calendar_id, safe='')}/events"
    )

    try:
        for index, user_id in enumerate(users[:2], start=1):
            start_time = datetime.combine(
                event_day,
                time(hour=3, minute=10 * index),
                tzinfo=timezone,
            )
            end_time = start_time + timedelta(minutes=5)
            tokens = set_session_vars(
                platform="line",
                chat_id=user_id,
                chat_type="dm",
                user_id=user_id,
                message_id=f"kumi-google-write-probe-{run_key}-{index}",
            )
            try:
                create_result = json.loads(handle_create_family_calendar_event({
                    "title": f"Kumi Agent 自動驗收 #{index}",
                    "start": start_time.isoformat(),
                    "end": end_time.isoformat(),
                    "description": "Temporary automated acceptance probe; safe to remove.",
                    "confirmed": True,
                }))
                event_id = str(((create_result.get("data") or {}).get("id") or ""))
                if create_result.get("ok") is not True or not event_id:
                    raise RuntimeError(f"Actor {index} calendar write failed")
                created_ids.append(event_id)

                list_result = json.loads(handle_list_family_calendar_events({
                    "start_date": event_day.isoformat(),
                    "end_date": (event_day + timedelta(days=1)).isoformat(),
                    "max_results": 100,
                }))
                visible_ids = {
                    str(item.get("id"))
                    for item in (list_result.get("data") or [])
                    if isinstance(item, dict)
                }
                actor_results.append({
                    "actor": f"allowed_line_actor_{index}",
                    "calendar_write_ok": True,
                    "calendar_readback_ok": event_id in visible_ids,
                })
            finally:
                clear_session_vars(tokens)
    finally:
        cleanup_ok = True
        for event_id in created_ids:
            response = cleanup_client._session.delete(
                f"{base_url}/{quote(event_id, safe='')}",
                params={"sendUpdates": "none"},
                timeout=15,
            )
            cleanup_ok = cleanup_ok and response.status_code in {204, 404, 410}
        cleanup_client.close()

    ok = (
        len(actor_results) == 2
        and all(
            item["calendar_write_ok"] and item["calendar_readback_ok"]
            for item in actor_results
        )
        and cleanup_ok
    )
    print(json.dumps({
        "ok": ok,
        "mutations_enabled": config.mutations_enabled,
        "trusted_line_actor_results": actor_results,
        "temporary_events_created": len(created_ids),
        "temporary_events_removed": cleanup_ok,
    }, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
