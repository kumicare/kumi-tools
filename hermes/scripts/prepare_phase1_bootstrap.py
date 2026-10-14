"""Prepare sensitive Phase 1 bootstrap values without printing them.

This operator helper runs on the Hermes host, reads the existing LINE allowlist,
and writes a mode-0600 JSON file for a one-time database bootstrap. It never
logs complete LINE IDs or the channel token.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

def parse_ids(value: str) -> list[str]:
    return [item.strip() for item in value.replace(";", ",").split(",") if item.strip()]


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
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--integration-auth-uid", required=True)
    args = parser.parse_args()

    env = read_dotenv(Path.home() / ".hermes" / ".env")
    users = parse_ids(env.get("LINE_ALLOWED_USERS", ""))
    groups = parse_ids(env.get("LINE_ALLOWED_GROUPS", ""))
    if len(users) != 2:
        raise RuntimeError(f"expected exactly two LINE users, found {len(users)}")
    if len(groups) != 1:
        raise RuntimeError(f"expected exactly one LINE group, found {len(groups)}")

    payload = {
        "integration_auth_uid": args.integration_auth_uid,
        "louis_line_user_id": users[0],
        "jacee_line_user_id": users[1],
        "sibling_group_id": groups[0],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)
        handle.write("\n")
    os.chmod(args.output, 0o600)
    print(f"wrote protected bootstrap values for {len(users)} users and {len(groups)} group")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
