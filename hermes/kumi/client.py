"""Small authenticated client for Kumi's named Supabase RPC boundary."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import unicodedata
from dataclasses import dataclass
from datetime import date
from typing import Any, Mapping

import httpx


class KumiConfigError(RuntimeError):
    pass


class KumiUpstreamError(RuntimeError):
    pass


@dataclass(frozen=True)
class KumiConfig:
    supabase_url: str
    publishable_key: str
    email: str
    password: str
    mutations_enabled: bool

    @classmethod
    def from_env(cls) -> "KumiConfig":
        values = {
            "supabase_url": os.getenv("KUMI_SUPABASE_URL", "").strip().rstrip("/"),
            "publishable_key": os.getenv("KUMI_SUPABASE_PUBLISHABLE_KEY", "").strip(),
            "email": os.getenv("KUMI_SUPABASE_EMAIL", "").strip(),
            "password": os.getenv("KUMI_SUPABASE_PASSWORD", ""),
        }
        missing = [name for name, value in values.items() if not value]
        if missing:
            raise KumiConfigError(f"Missing Kumi configuration: {', '.join(missing)}")
        if not values["supabase_url"].startswith("https://"):
            raise KumiConfigError("KUMI_SUPABASE_URL must use HTTPS")
        return cls(
            **values,
            mutations_enabled=os.getenv("KUMI_MUTATIONS_ENABLED", "false").strip().lower()
            in {"1", "true", "yes", "on"},
        )


def normalize_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFC", value).strip().split())


def normalize_member_name(value: str) -> str:
    return normalize_text(value).casefold()


def canonical_payload(payload: Mapping[str, Any]) -> bytes:
    def normalize(value: Any) -> Any:
        if isinstance(value, str):
            return unicodedata.normalize("NFC", value)
        if isinstance(value, date):
            return value.isoformat()
        if isinstance(value, Mapping):
            return {key: normalize(item) for key, item in value.items() if item is not None}
        if isinstance(value, list):
            return [normalize(item) for item in value]
        return value

    return json.dumps(
        normalize(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def payload_hash(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_payload(payload)).hexdigest()


def operation_key(platform: str, chat_id: str, message_id: str, tool_name: str) -> str:
    if not all((platform, chat_id, message_id, tool_name)):
        raise ValueError("Trusted session context is incomplete")
    raw = "\x1f".join((platform, chat_id, message_id, tool_name)).encode("utf-8")
    return f"v1:{hashlib.sha256(raw).hexdigest()}"


class SupabaseRpcClient:
    """Password-authenticated, fail-closed RPC client with serialized refresh."""

    def __init__(self, config: KumiConfig, *, transport: httpx.BaseTransport | None = None):
        self.config = config
        self._http = httpx.Client(timeout=15.0, transport=transport)
        self._lock = threading.Lock()
        self._access_token = ""
        self._refresh_token = ""
        self._expires_at = 0.0

    def close(self) -> None:
        self._http.close()

    def _auth_headers(self) -> dict[str, str]:
        return {"apikey": self.config.publishable_key, "Content-Type": "application/json"}

    def _apply_session(self, body: Mapping[str, Any]) -> str:
        access_token = str(body.get("access_token") or "")
        refresh_token = str(body.get("refresh_token") or "")
        expires_in = int(body.get("expires_in") or 0)
        if not access_token or not refresh_token or expires_in <= 0:
            raise KumiUpstreamError("Supabase Auth returned an incomplete session")
        self._access_token = access_token
        self._refresh_token = refresh_token
        self._expires_at = time.monotonic() + expires_in
        return access_token

    def _request_session(self) -> str:
        endpoint = f"{self.config.supabase_url}/auth/v1/token?grant_type=password"
        response = self._http.post(
            endpoint,
            headers=self._auth_headers(),
            json={"email": self.config.email, "password": self.config.password},
        )
        if response.status_code >= 400:
            raise KumiUpstreamError(f"Supabase Auth login failed ({response.status_code})")
        return self._apply_session(response.json())

    def _refresh_session(self) -> str:
        endpoint = f"{self.config.supabase_url}/auth/v1/token?grant_type=refresh_token"
        response = self._http.post(
            endpoint,
            headers=self._auth_headers(),
            json={"refresh_token": self._refresh_token},
        )
        if response.status_code >= 400:
            self._access_token = ""
            self._refresh_token = ""
            self._expires_at = 0.0
            raise KumiUpstreamError(f"Supabase Auth refresh failed ({response.status_code})")
        return self._apply_session(response.json())

    def access_token(self) -> str:
        if self._access_token and time.monotonic() < self._expires_at - 60:
            return self._access_token
        with self._lock:
            if self._access_token and time.monotonic() < self._expires_at - 60:
                return self._access_token
            if self._refresh_token:
                return self._refresh_session()
            return self._request_session()

    def rpc(self, name: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        token = self.access_token()
        response = self._http.post(
            f"{self.config.supabase_url}/rest/v1/rpc/{name}",
            headers={
                "apikey": self.config.publishable_key,
                "Authorization": f"Bearer {token}",
                "Content-Profile": "kumi_core",
                "Accept-Profile": "kumi_core",
                "Content-Type": "application/json",
            },
            json=dict(payload),
        )
        if response.status_code in {401, 403}:
            with self._lock:
                self._expires_at = 0.0
            raise KumiUpstreamError(f"Supabase RPC authorization failed ({response.status_code})")
        if response.status_code >= 400:
            raise KumiUpstreamError(f"Supabase RPC {name} failed ({response.status_code})")
        body = response.json()
        if not isinstance(body, dict) or "ok" not in body:
            raise KumiUpstreamError(f"Supabase RPC {name} returned an invalid envelope")
        return body
