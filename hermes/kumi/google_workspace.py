"""Least-privilege Google Sheets and Calendar client for Kumi."""

from __future__ import annotations

import base64
import hashlib
import os
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from typing import Any, Mapping, Protocol
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .client import KumiConfigError, KumiUpstreamError


class HttpSession(Protocol):
    def get(self, url: str, **kwargs): ...
    def post(self, url: str, **kwargs): ...
    def close(self) -> None: ...


@dataclass(frozen=True)
class GoogleWorkspaceConfig:
    service_account_file: str
    spreadsheet_id: str
    calendar_id: str
    allowed_sheets: tuple[str, ...]
    timezone: str
    mutations_enabled: bool

    @classmethod
    def from_env(cls) -> "GoogleWorkspaceConfig":
        service_account_file = os.getenv("KUMI_GOOGLE_SERVICE_ACCOUNT_FILE", "").strip()
        spreadsheet_id = os.getenv("KUMI_GOOGLE_SPREADSHEET_ID", "").strip()
        calendar_id = os.getenv("KUMI_GOOGLE_CALENDAR_ID", "").strip()
        allowed_sheets = tuple(
            item.strip()
            for item in os.getenv("KUMI_GOOGLE_ALLOWED_SHEETS", "").split(",")
            if item.strip()
        )
        timezone = os.getenv("KUMI_GOOGLE_TIMEZONE", "Asia/Taipei").strip()
        missing = []
        if not service_account_file:
            missing.append("KUMI_GOOGLE_SERVICE_ACCOUNT_FILE")
        if not spreadsheet_id:
            missing.append("KUMI_GOOGLE_SPREADSHEET_ID")
        if not calendar_id:
            missing.append("KUMI_GOOGLE_CALENDAR_ID")
        if not allowed_sheets:
            missing.append("KUMI_GOOGLE_ALLOWED_SHEETS")
        if missing:
            raise KumiConfigError(f"Missing Google Workspace configuration: {', '.join(missing)}")
        if not Path(service_account_file).is_absolute():
            raise KumiConfigError("KUMI_GOOGLE_SERVICE_ACCOUNT_FILE must be an absolute path")
        try:
            ZoneInfo(timezone)
        except ZoneInfoNotFoundError as exc:
            raise KumiConfigError("KUMI_GOOGLE_TIMEZONE must be a valid IANA timezone") from exc
        return cls(
            service_account_file=service_account_file,
            spreadsheet_id=spreadsheet_id,
            calendar_id=calendar_id,
            allowed_sheets=allowed_sheets,
            timezone=timezone,
            mutations_enabled=os.getenv("KUMI_GOOGLE_MUTATIONS_ENABLED", "false").strip().lower()
            in {"1", "true", "yes", "on"},
        )


def calendar_event_id(operation_key: str) -> str:
    """Create a Google-compatible base32hex event ID from an operation key."""
    digest = hashlib.sha256(operation_key.encode("utf-8")).digest()
    return "kumi" + base64.b32hexencode(digest).decode("ascii").lower().rstrip("=")


def _rfc3339_boundary(value: date, tz: ZoneInfo) -> str:
    return datetime.combine(value, time.min, tzinfo=tz).isoformat()


class GoogleWorkspaceClient:
    SHEETS_BASE = "https://sheets.googleapis.com/v4/spreadsheets"
    CALENDAR_BASE = "https://www.googleapis.com/calendar/v3/calendars"

    def __init__(self, config: GoogleWorkspaceConfig, *, session: HttpSession | None = None):
        self.config = config
        self._session = session or self._authorized_session(config)

    @staticmethod
    def _authorized_session(config: GoogleWorkspaceConfig) -> HttpSession:
        try:
            from google.auth.transport.requests import AuthorizedSession
            from google.oauth2 import service_account
        except ImportError as exc:
            raise KumiConfigError("Google Workspace dependencies are not installed") from exc
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets.readonly",
            "https://www.googleapis.com/auth/calendar.events.readonly",
        ]
        if config.mutations_enabled:
            scopes = [
                "https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/calendar.events",
            ]
        credentials = service_account.Credentials.from_service_account_file(
            config.service_account_file,
            scopes=scopes,
        )
        return AuthorizedSession(credentials)

    def close(self) -> None:
        self._session.close()

    @staticmethod
    def _json(response, operation: str) -> Mapping[str, Any]:
        if response.status_code >= 400:
            raise KumiUpstreamError(f"Google Workspace {operation} failed ({response.status_code})")
        body = response.json()
        if not isinstance(body, Mapping):
            raise KumiUpstreamError(f"Google Workspace {operation} returned invalid JSON")
        return body

    def read_sheet(self, sheet_name: str, *, max_rows: int = 100) -> dict[str, Any]:
        if sheet_name not in self.config.allowed_sheets:
            raise ValueError("sheet_not_allowed: this worksheet is not enabled for Kumi")
        if not 1 <= max_rows <= 200:
            raise ValueError("invalid_input: max_rows must be between 1 and 200")
        a1_range = f"'{sheet_name.replace(chr(39), chr(39) * 2)}'!A1:Z{max_rows}"
        encoded_range = quote(a1_range, safe="")
        response = self._session.get(
            f"{self.SHEETS_BASE}/{quote(self.config.spreadsheet_id, safe='')}/values/{encoded_range}",
            params={"majorDimension": "ROWS", "valueRenderOption": "FORMATTED_VALUE"},
            timeout=15,
        )
        body = self._json(response, "sheet read")
        values = body.get("values") if isinstance(body.get("values"), list) else []
        return {
            "ok": True,
            "data": {
                "sheet": sheet_name,
                "range": str(body.get("range") or a1_range),
                "rows": values,
                "row_count": len(values),
            },
        }

    def list_calendar_events(
        self,
        start_date: date,
        end_date: date,
        *,
        max_results: int = 50,
    ) -> dict[str, Any]:
        if end_date <= start_date:
            raise ValueError("invalid_input: end_date must be after start_date")
        if (end_date - start_date).days > 366:
            raise ValueError("invalid_input: calendar range cannot exceed 366 days")
        if not 1 <= max_results <= 100:
            raise ValueError("invalid_input: max_results must be between 1 and 100")
        tz = ZoneInfo(self.config.timezone)
        response = self._session.get(
            f"{self.CALENDAR_BASE}/{quote(self.config.calendar_id, safe='')}/events",
            params={
                "timeMin": _rfc3339_boundary(start_date, tz),
                "timeMax": _rfc3339_boundary(end_date, tz),
                "singleEvents": "true",
                "orderBy": "startTime",
                "maxResults": max_results,
                "timeZone": self.config.timezone,
            },
            timeout=15,
        )
        body = self._json(response, "calendar read")
        items = body.get("items") if isinstance(body.get("items"), list) else []
        events = []
        for item in items:
            if not isinstance(item, Mapping) or item.get("status") == "cancelled":
                continue
            events.append({
                "id": item.get("id"),
                "title": item.get("summary") or "(untitled)",
                "start": item.get("start"),
                "end": item.get("end"),
                "location": item.get("location"),
                "status": item.get("status"),
                "html_link": item.get("htmlLink"),
            })
        return {"ok": True, "data": events}

    def create_calendar_event(
        self,
        *,
        operation_key: str,
        title: str,
        start: str,
        end: str,
        location: str | None,
        description: str | None,
    ) -> dict[str, Any]:
        if not self.config.mutations_enabled:
            raise KumiConfigError("Google Workspace mutations are disabled")
        event_id = calendar_event_id(operation_key)
        event = {
            "id": event_id,
            "summary": title,
            "start": {"dateTime": start, "timeZone": self.config.timezone},
            "end": {"dateTime": end, "timeZone": self.config.timezone},
            "extendedProperties": {"private": {"kumiOperationKey": operation_key}},
        }
        if location:
            event["location"] = location
        if description:
            event["description"] = description
        base_url = f"{self.CALENDAR_BASE}/{quote(self.config.calendar_id, safe='')}/events"
        response = self._session.post(
            base_url,
            params={"sendUpdates": "none"},
            json=event,
            timeout=15,
        )
        if response.status_code == 409:
            existing = self._session.get(f"{base_url}/{event_id}", timeout=15)
            body = self._json(existing, "calendar idempotency read")
            private = ((body.get("extendedProperties") or {}).get("private") or {})
            if private.get("kumiOperationKey") != operation_key:
                raise KumiUpstreamError("Google Calendar event ID conflict")
        else:
            body = self._json(response, "calendar write")
        return {
            "ok": True,
            "data": {
                "id": body.get("id"),
                "title": body.get("summary"),
                "start": body.get("start"),
                "end": body.get("end"),
                "location": body.get("location"),
                "html_link": body.get("htmlLink"),
            },
        }
