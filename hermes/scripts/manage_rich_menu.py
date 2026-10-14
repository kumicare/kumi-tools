"""Deploy, inspect, or roll back Kumi's LINE Rich Menu.

The script reads the existing channel access token from ``~/.hermes/.env``.
It never prints the token and stores only non-secret menu IDs and content
hashes in the operator state file.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import struct
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

API = "https://api.line.me/v2/bot"
DATA_API = "https://api-data.line.me/v2/bot"


def read_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def validate_local(menu: dict[str, Any], image: bytes, image_path: Path) -> None:
    size = menu.get("size") or {}
    width, height = size.get("width"), size.get("height")
    if width != 2500 or height != 843:
        raise ValueError("Kumi Phase 1 Rich Menu must be exactly 2500x843")
    if not 1 <= len(menu.get("areas") or []) <= 20:
        raise ValueError("Rich Menu must contain 1-20 areas")
    if len(image) > 1_000_000:
        raise ValueError("Rich Menu image exceeds LINE's 1 MB limit")
    if image_path.suffix.lower() != ".png":
        raise ValueError("Kumi Phase 1 Rich Menu image must be PNG")
    if len(image) < 24 or image[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("Rich Menu image is not a valid PNG")
    image_width, image_height = struct.unpack(">II", image[16:24])
    if (image_width, image_height) != (width, height):
        raise ValueError("Rich Menu image dimensions don't match the menu object")
    bounds_list: list[dict[str, int]] = []
    for area in menu["areas"]:
        bounds = area.get("bounds") or {}
        if any(not isinstance(bounds.get(key), int) for key in ("x", "y", "width", "height")):
            raise ValueError("Every Rich Menu area needs integer bounds")
        if bounds["x"] < 0 or bounds["y"] < 0:
            raise ValueError("Rich Menu bounds cannot be negative")
        if bounds["x"] + bounds["width"] > width or bounds["y"] + bounds["height"] > height:
            raise ValueError("Rich Menu area exceeds the image bounds")
        bounds_list.append(bounds)
        action = area.get("action") or {}
        if action.get("type") != "message" or not action.get("text"):
            raise ValueError("Phase 1.2 supports message actions only")
    for index, first in enumerate(bounds_list):
        for second in bounds_list[index + 1:]:
            overlaps = (
                first["x"] < second["x"] + second["width"]
                and second["x"] < first["x"] + first["width"]
                and first["y"] < second["y"] + second["height"]
                and second["y"] < first["y"] + first["height"]
            )
            if overlaps:
                raise ValueError("Rich Menu areas cannot overlap")
    covered_area = sum(bounds["width"] * bounds["height"] for bounds in bounds_list)
    if covered_area != width * height:
        raise ValueError("Kumi Phase 1 Rich Menu areas must cover the full image")


class LineRichMenuClient:
    def __init__(self, token: str):
        self.http = httpx.Client(
            timeout=20,
            headers={"Authorization": f"Bearer {token}"},
        )

    def close(self) -> None:
        self.http.close()

    def _json(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        response = self.http.request(method, url, **kwargs)
        if response.status_code >= 400:
            raise RuntimeError(f"LINE Rich Menu API failed: {method} {url} ({response.status_code})")
        if not response.content:
            return {}
        body = response.json()
        return body if isinstance(body, dict) else {}

    def current_default(self) -> dict[str, Any]:
        response = self.http.get(f"{API}/user/all/richmenu")
        if response.status_code == 404:
            return {"owner": "none", "richMenuId": None}
        if response.status_code == 403:
            return {"owner": "official-account-manager", "richMenuId": None}
        if response.status_code >= 400:
            raise RuntimeError(f"LINE default Rich Menu lookup failed ({response.status_code})")
        return {"owner": "messaging-api", "richMenuId": response.json().get("richMenuId")}

    def list_menus(self) -> list[dict[str, Any]]:
        return self._json("GET", f"{API}/richmenu/list").get("richmenus", [])

    def get_menu(self, rich_menu_id: str) -> dict[str, Any]:
        return self._json("GET", f"{API}/richmenu/{rich_menu_id}")

    def get_image(self, rich_menu_id: str) -> bytes:
        response = self.http.get(f"{DATA_API}/richmenu/{rich_menu_id}/content")
        if response.status_code >= 400:
            raise RuntimeError(f"LINE Rich Menu image lookup failed ({response.status_code})")
        return response.content

    def validate(self, menu: dict[str, Any]) -> None:
        self._json("POST", f"{API}/richmenu/validate", json=menu)

    def create(self, menu: dict[str, Any]) -> str:
        rich_menu_id = self._json("POST", f"{API}/richmenu", json=menu).get("richMenuId")
        if not rich_menu_id:
            raise RuntimeError("LINE didn't return a Rich Menu ID")
        return str(rich_menu_id)

    def upload(self, rich_menu_id: str, image: bytes, content_type: str) -> None:
        self._json(
            "POST",
            f"{DATA_API}/richmenu/{rich_menu_id}/content",
            content=image,
            headers={"Content-Type": content_type},
        )

    def set_default(self, rich_menu_id: str) -> None:
        self._json("POST", f"{API}/user/all/richmenu/{rich_menu_id}")

    def clear_default(self) -> None:
        self._json("DELETE", f"{API}/user/all/richmenu")

    def delete(self, rich_menu_id: str) -> None:
        self._json("DELETE", f"{API}/richmenu/{rich_menu_id}")


def protected_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    path.chmod(0o600)


def status(client: LineRichMenuClient) -> dict[str, Any]:
    current = client.current_default()
    menus = client.list_menus()
    managed = [
        {"richMenuId": menu.get("richMenuId"), "name": menu.get("name")}
        for menu in menus
        if str(menu.get("name") or "").startswith("Kumi ")
    ]
    return {"default": current, "kumiMenus": managed, "kumiMenuCount": len(managed)}


def deploy(
    client: LineRichMenuClient,
    menu_path: Path,
    image_path: Path,
    state_path: Path,
) -> dict[str, Any]:
    menu_bytes = menu_path.read_bytes()
    image = image_path.read_bytes()
    menu = json.loads(menu_bytes)
    validate_local(menu, image, image_path)
    client.validate(menu)

    current = client.current_default()
    previous_default = current
    superseded_rich_menu_id: str | None = None
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if (
            current.get("richMenuId") == state.get("deployedRichMenuId")
            and state.get("menuSha256") == digest(menu_bytes)
            and state.get("imageSha256") == digest(image)
        ):
            return {"changed": False, "richMenuId": current.get("richMenuId"), "reason": "already-current"}
        if current.get("richMenuId") == state.get("deployedRichMenuId"):
            previous_default = state.get("previousDefault") or {"owner": "none", "richMenuId": None}
            superseded_rich_menu_id = str(current["richMenuId"])

    rich_menu_id = client.create(menu)
    try:
        content_type = mimetypes.guess_type(image_path.name)[0] or "image/png"
        client.upload(rich_menu_id, image, content_type)
        client.set_default(rich_menu_id)
    except Exception:
        client.delete(rich_menu_id)
        raise

    protected_write(state_path, {
        "deployedAt": datetime.now(timezone.utc).isoformat(),
        "deployedRichMenuId": rich_menu_id,
        "previousDefault": previous_default,
        "menuSha256": digest(menu_bytes),
        "imageSha256": digest(image),
    })
    superseded_deleted = False
    if superseded_rich_menu_id:
        try:
            client.delete(superseded_rich_menu_id)
            superseded_deleted = True
        except RuntimeError:
            pass
    return {
        "changed": True,
        "richMenuId": rich_menu_id,
        "previousDefault": previous_default,
        "supersededDeleted": superseded_deleted,
    }


def rollback(client: LineRichMenuClient, state_path: Path) -> dict[str, Any]:
    if not state_path.exists():
        raise RuntimeError("No Kumi Rich Menu deployment state exists")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    deployed_id = state.get("deployedRichMenuId")
    current = client.current_default()
    if current.get("richMenuId") != deployed_id:
        raise RuntimeError("Current default no longer matches this deployment; refusing rollback")
    previous = state.get("previousDefault") or {}
    if previous.get("owner") == "messaging-api" and previous.get("richMenuId"):
        client.set_default(str(previous["richMenuId"]))
    else:
        client.clear_default()
    return {"rolledBack": True, "restored": previous, "retainedRichMenuId": deployed_id}


def verify(
    client: LineRichMenuClient,
    menu_path: Path,
    image_path: Path,
    state_path: Path,
) -> dict[str, Any]:
    if not state_path.exists():
        raise RuntimeError("No Kumi Rich Menu deployment state exists")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    rich_menu_id = str(state.get("deployedRichMenuId") or "")
    if not rich_menu_id:
        raise RuntimeError("Deployment state is missing its Rich Menu ID")
    expected_menu = json.loads(menu_path.read_text(encoding="utf-8"))
    expected_image = image_path.read_bytes()
    validate_local(expected_menu, expected_image, image_path)

    current = client.current_default()
    remote_menu = client.get_menu(rich_menu_id)
    remote_menu.pop("richMenuId", None)
    remote_image = client.get_image(rich_menu_id)
    checks = {
        "isDefault": current.get("richMenuId") == rich_menu_id,
        "menuMatches": remote_menu == expected_menu,
        "imageMatches": digest(remote_image) == digest(expected_image),
    }
    if not all(checks.values()):
        raise RuntimeError(f"LINE Rich Menu verification failed: {checks}")
    return {"ok": True, "richMenuId": rich_menu_id, **checks}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("check", "status", "deploy", "verify", "rollback"))
    parser.add_argument("--menu", type=Path)
    parser.add_argument("--image", type=Path)
    parser.add_argument(
        "--state",
        type=Path,
        default=Path.home() / ".config" / "kumi" / "rich-menu-state.json",
    )
    args = parser.parse_args()

    if args.command == "check":
        if not args.menu or not args.image:
            parser.error("check requires --menu and --image")
        menu = json.loads(args.menu.read_text(encoding="utf-8"))
        image = args.image.read_bytes()
        validate_local(menu, image, args.image)
        print(json.dumps({
            "ok": True,
            "areas": len(menu["areas"]),
            "imageBytes": len(image),
            "menuSha256": digest(args.menu.read_bytes()),
            "imageSha256": digest(image),
        }))
        return 0

    token = read_dotenv(Path.home() / ".hermes" / ".env").get("LINE_CHANNEL_ACCESS_TOKEN", "")
    if not token:
        raise RuntimeError("LINE_CHANNEL_ACCESS_TOKEN is unavailable")
    client = LineRichMenuClient(token)
    try:
        if args.command == "status":
            result = status(client)
        elif args.command == "deploy":
            if not args.menu or not args.image:
                parser.error("deploy requires --menu and --image")
            result = deploy(client, args.menu, args.image, args.state)
        elif args.command == "verify":
            if not args.menu or not args.image:
                parser.error("verify requires --menu and --image")
            result = verify(client, args.menu, args.image, args.state)
        else:
            result = rollback(client, args.state)
    finally:
        client.close()
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
