"""Hermes tool schemas and handlers for Kumi family tasks."""

from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import date, datetime
from typing import Any

from gateway.session_context import get_session_env
from tools.registry import tool_error, tool_result

from .client import (
    KumiConfig,
    KumiConfigError,
    KumiUpstreamError,
    SupabaseRpcClient,
    normalize_member_name,
    normalize_text,
    operation_key,
    payload_hash,
)
from .google_workspace import GoogleWorkspaceClient, GoogleWorkspaceConfig

_CLIENT: SupabaseRpcClient | None = None
_CLIENT_LOCK = threading.Lock()
_GOOGLE_CLIENT: GoogleWorkspaceClient | None = None
_GOOGLE_CLIENT_LOCK = threading.Lock()


def _config_available() -> bool:
    try:
        KumiConfig.from_env()
        return True
    except KumiConfigError:
        return False


def _mutations_available() -> bool:
    try:
        return KumiConfig.from_env().mutations_enabled
    except KumiConfigError:
        return False


def _google_config_available() -> bool:
    try:
        GoogleWorkspaceConfig.from_env()
        return True
    except KumiConfigError:
        return False


def _google_mutations_available() -> bool:
    try:
        return GoogleWorkspaceConfig.from_env().mutations_enabled
    except KumiConfigError:
        return False


def _client() -> SupabaseRpcClient:
    global _CLIENT
    if _CLIENT is None:
        with _CLIENT_LOCK:
            if _CLIENT is None:
                _CLIENT = SupabaseRpcClient(KumiConfig.from_env())
    return _CLIENT


def _google_client() -> GoogleWorkspaceClient:
    global _GOOGLE_CLIENT
    if _GOOGLE_CLIENT is None:
        with _GOOGLE_CLIENT_LOCK:
            if _GOOGLE_CLIENT is None:
                _GOOGLE_CLIENT = GoogleWorkspaceClient(GoogleWorkspaceConfig.from_env())
    return _GOOGLE_CLIENT


def _source() -> dict[str, str]:
    platform = get_session_env("HERMES_SESSION_PLATFORM", "").strip().lower()
    chat_id = get_session_env("HERMES_SESSION_CHAT_ID", "").strip()
    chat_type = get_session_env("HERMES_SESSION_CHAT_TYPE", "").strip().lower()
    user_id = get_session_env("HERMES_SESSION_USER_ID", "").strip()
    message_id = get_session_env("HERMES_SESSION_MESSAGE_ID", "").strip()
    if platform != "line" or not all((chat_id, user_id, message_id)):
        raise ValueError("Kumi tools require a verified LINE message context")
    normalized_chat_type = {"dm": "private", "private": "private", "group": "group", "room": "room"}.get(chat_type)
    if not normalized_chat_type:
        raise ValueError("Unsupported LINE chat type")
    return {
        "p_provider": platform,
        "p_provider_user_id": user_id,
        "p_chat_type": normalized_chat_type,
        "p_chat_id": chat_id,
        "message_id": message_id,
    }


def _rpc_source(source: dict[str, str]) -> dict[str, str]:
    return {key: value for key, value in source.items() if key != "message_id"}


def _result(body: dict[str, Any]) -> str:
    if body.get("ok") is True:
        return tool_result(body)
    error = body.get("error") if isinstance(body.get("error"), dict) else {}
    code = str(error.get("code") or "upstream_unavailable")
    message = str(error.get("message") or "Kumi operation failed")
    latest = error.get("latest")
    suffix = f" Latest task: {json.dumps(latest, ensure_ascii=False)}" if latest else ""
    return tool_error(f"{code}: {message}{suffix}")


def _safe_handler(fn):
    def wrapped(args: dict, **kwargs) -> str:
        try:
            return fn(args or {})
        except (KumiConfigError, KumiUpstreamError, ValueError) as exc:
            return tool_error(str(exc))
        except Exception as exc:
            return tool_error(f"Kumi tool failed safely: {type(exc).__name__}")

    return wrapped


def _resolve_member(source: dict[str, str], raw: str) -> str:
    value = normalize_text(str(raw or ""))
    try:
        return str(uuid.UUID(value))
    except ValueError:
        pass
    body = _client().rpc("list_family_members", _rpc_source(source))
    if body.get("ok") is not True:
        raise ValueError("Cannot resolve assignee because the member list is unavailable")
    wanted = normalize_member_name(value)
    matches = [member for member in body.get("data", []) if normalize_member_name(str(member.get("name") or "")) == wanted]
    if len(matches) != 1:
        raise ValueError("ambiguous_member: assignee must match exactly one active household member")
    return str(matches[0]["id"])


def _mutation_payload(tool_name: str, args: dict[str, Any], source: dict[str, str]) -> dict[str, Any]:
    return {
        **_rpc_source(source),
        "p_operation_key": operation_key("line", source["p_chat_id"], source["message_id"], tool_name),
        "p_payload_hash": payload_hash(args),
    }


@_safe_handler
def handle_list_family_members(args: dict) -> str:
    source = _source()
    return _result(_client().rpc("list_family_members", _rpc_source(source)))


@_safe_handler
def handle_create_family_task(args: dict) -> str:
    source = _source()
    title = normalize_text(str(args.get("title") or ""))
    if not 1 <= len(title) <= 200:
        raise ValueError("invalid_input: title must contain 1-200 characters")
    due_date = args.get("due_date")
    if due_date:
        date.fromisoformat(str(due_date))
    validated: dict[str, Any] = {"title": title, "due_date": due_date or None}
    if args.get("assignee"):
        validated["assignee_member_id"] = _resolve_member(source, str(args["assignee"]))
    payload = {
        **_mutation_payload("create_family_task", validated, source),
        "p_title": title,
        "p_assignee_member_id": validated.get("assignee_member_id"),
        "p_due_date": due_date or None,
    }
    return _result(_client().rpc("create_family_task", payload))


@_safe_handler
def handle_list_family_tasks(args: dict) -> str:
    source = _source()
    payload = {
        **_rpc_source(source),
        "p_scope": args.get("scope"),
        "p_status": args.get("status", "open"),
        "p_limit": int(args.get("limit", 50)),
    }
    return _result(_client().rpc("list_family_tasks", payload))


@_safe_handler
def handle_assign_family_task(args: dict) -> str:
    source = _source()
    task_id = str(uuid.UUID(str(args.get("task_id") or "")))
    assignee_id = _resolve_member(source, str(args.get("assignee") or ""))
    validated = {
        "task_id": task_id,
        "assignee_member_id": assignee_id,
        "expected_version": int(args.get("expected_version")),
    }
    payload = {
        **_mutation_payload("assign_family_task", validated, source),
        "p_task_id": task_id,
        "p_assignee_member_id": assignee_id,
        "p_expected_version": validated["expected_version"],
    }
    return _result(_client().rpc("assign_family_task", payload))


@_safe_handler
def handle_claim_family_task(args: dict) -> str:
    return _handle_task_transition("claim_family_task", args)


@_safe_handler
def handle_complete_family_task(args: dict) -> str:
    return _handle_task_transition("complete_family_task", args)


@_safe_handler
def handle_read_family_sheet(args: dict) -> str:
    _source()
    sheet = normalize_text(str(args.get("sheet") or ""))
    if not sheet:
        raise ValueError("invalid_input: sheet is required")
    max_rows = int(args.get("max_rows", 100))
    return _result(_google_client().read_sheet(sheet, max_rows=max_rows))


@_safe_handler
def handle_list_family_calendar_events(args: dict) -> str:
    _source()
    start_date = date.fromisoformat(str(args.get("start_date") or ""))
    end_date = date.fromisoformat(str(args.get("end_date") or ""))
    max_results = int(args.get("max_results", 50))
    return _result(
        _google_client().list_calendar_events(
            start_date,
            end_date,
            max_results=max_results,
        )
    )


@_safe_handler
def handle_create_family_calendar_event(args: dict) -> str:
    source = _source()
    if args.get("confirmed") is not True:
        raise ValueError("confirmation_required: ask the user to confirm the exact event before writing")
    title = normalize_text(str(args.get("title") or ""))
    if not 1 <= len(title) <= 200:
        raise ValueError("invalid_input: title must contain 1-200 characters")
    start = str(args.get("start") or "")
    end = str(args.get("end") or "")
    parsed_start = datetime.fromisoformat(start.replace("Z", "+00:00"))
    parsed_end = datetime.fromisoformat(end.replace("Z", "+00:00"))
    if parsed_start.tzinfo is None or parsed_end.tzinfo is None:
        raise ValueError("invalid_input: start and end must include a UTC offset")
    if parsed_end <= parsed_start:
        raise ValueError("invalid_input: end must be after start")
    location = normalize_text(str(args.get("location") or "")) or None
    description = normalize_text(str(args.get("description") or "")) or None
    op_key = operation_key(
        "line",
        source["p_chat_id"],
        source["message_id"],
        "create_family_calendar_event",
    )
    return _result(
        _google_client().create_calendar_event(
            operation_key=op_key,
            title=title,
            start=start,
            end=end,
            location=location,
            description=description,
        )
    )


def _handle_task_transition(tool_name: str, args: dict) -> str:
    source = _source()
    validated = {
        "task_id": str(uuid.UUID(str(args.get("task_id") or ""))),
        "expected_version": int(args.get("expected_version")),
    }
    payload = {
        **_mutation_payload(tool_name, validated, source),
        "p_task_id": validated["task_id"],
        "p_expected_version": validated["expected_version"],
    }
    return _result(_client().rpc(tool_name, payload))


LIST_MEMBERS_SCHEMA = {
    "name": "list_family_members",
    "description": "List active members in the caller's authorized Kumi household.",
    "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
}
CREATE_TASK_SCHEMA = {
    "name": "create_family_task",
    "description": "Create one shared family task. Ask for clarification rather than guessing an assignee or date.",
    "parameters": {
        "type": "object",
        "properties": {
            "title": {"type": "string", "minLength": 1, "maxLength": 200},
            "assignee": {"type": "string", "description": "Exact member UUID or display name."},
            "due_date": {"type": "string", "format": "date"},
        },
        "required": ["title"],
        "additionalProperties": False,
    },
}
LIST_TASKS_SCHEMA = {
    "name": "list_family_tasks",
    "description": "List authorized shared family tasks. Private chat defaults to mine; group chat defaults to household.",
    "parameters": {
        "type": "object",
        "properties": {
            "scope": {"type": "string", "enum": ["mine", "household", "unassigned"]},
            "status": {"type": "string", "enum": ["open", "completed"], "default": "open"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 50},
        },
        "additionalProperties": False,
    },
}
ASSIGN_TASK_SCHEMA = {
    "name": "assign_family_task",
    "description": "Assign an open task using its UUID and current version.",
    "parameters": {
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "format": "uuid"},
            "assignee": {"type": "string", "description": "Exact member UUID or display name."},
            "expected_version": {"type": "integer", "minimum": 1},
        },
        "required": ["task_id", "assignee", "expected_version"],
        "additionalProperties": False,
    },
}
READ_FAMILY_SHEET_SCHEMA = {
    "name": "read_family_sheet",
    "description": (
        "Use this for every LINE request to query the family's Google Sheet or "
        "spreadsheet. It reads one operator-allowlisted worksheet through Kumi's "
        "service account; do not use terminal commands or ask for Google OAuth."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "sheet": {"type": "string", "description": "Exact allowlisted worksheet name."},
            "max_rows": {"type": "integer", "minimum": 1, "maximum": 200, "default": 100},
        },
        "required": ["sheet"],
        "additionalProperties": False,
    },
}
LIST_FAMILY_CALENDAR_SCHEMA = {
    "name": "list_family_calendar_events",
    "description": (
        "Use this for every LINE request to query the user's, family's, or Google "
        "Calendar schedule. It reads the configured primary calendar through Kumi's "
        "service account; do not use terminal commands or ask for Google OAuth."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "start_date": {"type": "string", "format": "date"},
            "end_date": {"type": "string", "format": "date", "description": "Exclusive end date."},
            "max_results": {"type": "integer", "minimum": 1, "maximum": 100, "default": 50},
        },
        "required": ["start_date", "end_date"],
        "additionalProperties": False,
    },
}
CREATE_FAMILY_CALENDAR_SCHEMA = {
    "name": "create_family_calendar_event",
    "description": (
        "Use this for every LINE request to add a Google Calendar event. Create one "
        "event in the configured primary calendar through Kumi's service account "
        "only after the user confirms its exact details; do not use terminal "
        "commands or ask for Google OAuth."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "title": {"type": "string", "minLength": 1, "maxLength": 200},
            "start": {"type": "string", "format": "date-time", "description": "RFC3339 timestamp with UTC offset."},
            "end": {"type": "string", "format": "date-time", "description": "RFC3339 timestamp with UTC offset."},
            "location": {"type": "string", "maxLength": 300},
            "description": {"type": "string", "maxLength": 1000},
            "confirmed": {"type": "boolean", "description": "True only after the user confirms title, date, start, and end."},
        },
        "required": ["title", "start", "end", "confirmed"],
        "additionalProperties": False,
    },
}


def transition_schema(name: str, description: str) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "format": "uuid"},
                "expected_version": {"type": "integer", "minimum": 1},
            },
            "required": ["task_id", "expected_version"],
            "additionalProperties": False,
        },
    }


TOOLS = (
    ("list_family_members", LIST_MEMBERS_SCHEMA, handle_list_family_members, "👪", _config_available),
    ("create_family_task", CREATE_TASK_SCHEMA, handle_create_family_task, "➕", _mutations_available),
    ("list_family_tasks", LIST_TASKS_SCHEMA, handle_list_family_tasks, "📋", _config_available),
    ("assign_family_task", ASSIGN_TASK_SCHEMA, handle_assign_family_task, "👉", _mutations_available),
    ("claim_family_task", transition_schema("claim_family_task", "Claim an unassigned open task for the current actor."), handle_claim_family_task, "🙋", _mutations_available),
    ("complete_family_task", transition_schema("complete_family_task", "Complete an authorized open task using its UUID and current version."), handle_complete_family_task, "✅", _mutations_available),
    ("read_family_sheet", READ_FAMILY_SHEET_SCHEMA, handle_read_family_sheet, "📄", _google_config_available),
    ("list_family_calendar_events", LIST_FAMILY_CALENDAR_SCHEMA, handle_list_family_calendar_events, "🗓️", _google_config_available),
    ("create_family_calendar_event", CREATE_FAMILY_CALENDAR_SCHEMA, handle_create_family_calendar_event, "📅", _google_mutations_available),
)
