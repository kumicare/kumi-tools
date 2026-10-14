import json
import sys
import types
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

SESSION = {}

gateway_module = types.ModuleType("gateway")
session_module = types.ModuleType("gateway.session_context")
session_module.get_session_env = lambda name, default="": SESSION.get(name, default)
gateway_module.session_context = session_module
sys.modules.setdefault("gateway", gateway_module)
sys.modules.setdefault("gateway.session_context", session_module)

registry_module = types.ModuleType("tools.registry")
registry_module.tool_result = lambda value: json.dumps({"kind": "result", "value": value}, ensure_ascii=False)
registry_module.tool_error = lambda value: json.dumps({"kind": "error", "value": value}, ensure_ascii=False)
tools_package = types.ModuleType("tools")
tools_package.registry = registry_module
sys.modules.setdefault("tools", tools_package)
sys.modules.setdefault("tools.registry", registry_module)

from kumi import tools as kumi_tools  # noqa: E402


class FakeClient:
    def __init__(self):
        self.calls = []

    def rpc(self, name, payload):
        self.calls.append((name, payload))
        if name == "list_family_members":
            return {"ok": True, "data": [{"id": "00000000-0000-4000-8000-000000000002", "name": "Jacee"}]}
        return {"ok": True, "data": {"id": "00000000-0000-4000-8000-000000000200"}}


class FakeGoogleClient:
    def __init__(self):
        self.calls = []

    def read_sheet(self, sheet, max_rows=100):
        self.calls.append(("read_sheet", sheet, max_rows))
        return {"ok": True, "data": {"sheet": sheet, "rows": [["資料"]]}}

    def list_calendar_events(self, start_date, end_date, max_results=50):
        self.calls.append(("list_calendar_events", start_date, end_date, max_results))
        return {"ok": True, "data": []}

    def create_calendar_event(self, **kwargs):
        self.calls.append(("create_calendar_event", kwargs))
        return {"ok": True, "data": {"id": "event-1", "title": kwargs["title"]}}


class HermesToolsTest(unittest.TestCase):
    def setUp(self):
        SESSION.clear()
        SESSION.update({
            "HERMES_SESSION_PLATFORM": "line",
            "HERMES_SESSION_CHAT_ID": "U-private",
            "HERMES_SESSION_CHAT_TYPE": "dm",
            "HERMES_SESSION_USER_ID": "U-louis",
            "HERMES_SESSION_MESSAGE_ID": "message-123",
        })
        self.client = FakeClient()
        self.google_client = FakeGoogleClient()
        kumi_tools._CLIENT = self.client
        kumi_tools._GOOGLE_CLIENT = self.google_client

    def tearDown(self):
        kumi_tools._CLIENT = None
        kumi_tools._GOOGLE_CLIENT = None

    def test_private_source_is_normalized_and_not_model_controlled(self):
        body = json.loads(kumi_tools.handle_create_family_task({
            "title": "  申請長照交通  ",
            "assignee": " jacee ",
            "due_date": "2026-08-09",
        }))
        self.assertEqual(body["kind"], "result")
        _, payload = self.client.calls[-1]
        self.assertEqual(payload["p_chat_type"], "private")
        self.assertEqual(payload["p_provider_user_id"], "U-louis")
        self.assertEqual(payload["p_assignee_member_id"], "00000000-0000-4000-8000-000000000002")
        self.assertNotIn("user_id", {"title": "申請長照交通"})

    def test_retry_has_stable_operation_key_and_payload_hash(self):
        args = {"title": "領慢性處方箋"}
        kumi_tools.handle_create_family_task(args)
        first = self.client.calls[-1][1]
        kumi_tools.handle_create_family_task(args)
        second = self.client.calls[-1][1]
        self.assertEqual(first["p_operation_key"], second["p_operation_key"])
        self.assertEqual(first["p_payload_hash"], second["p_payload_hash"])

    def test_group_source_is_preserved_for_rpc_authorization(self):
        SESSION["HERMES_SESSION_CHAT_ID"] = "C-siblings"
        SESSION["HERMES_SESSION_CHAT_TYPE"] = "group"
        kumi_tools.handle_list_family_tasks({})
        _, payload = self.client.calls[-1]
        self.assertEqual(payload["p_chat_type"], "group")
        self.assertEqual(payload["p_chat_id"], "C-siblings")
        self.assertIsNone(payload["p_scope"])

    def test_non_line_context_fails_closed(self):
        SESSION["HERMES_SESSION_PLATFORM"] = "cli"
        body = json.loads(kumi_tools.handle_list_family_tasks({}))
        self.assertEqual(body["kind"], "error")
        self.assertIn("verified LINE", body["value"])

    def test_ambiguous_member_fails_before_mutation(self):
        self.client.rpc = lambda name, payload: {"ok": True, "data": []}
        body = json.loads(kumi_tools.handle_create_family_task({"title": "任務", "assignee": "Nobody"}))
        self.assertEqual(body["kind"], "error")
        self.assertIn("ambiguous_member", body["value"])

    def test_sheet_read_keeps_resource_selection_out_of_model_control(self):
        body = json.loads(kumi_tools.handle_read_family_sheet({"sheet": "行政", "max_rows": 20}))
        self.assertEqual(body["kind"], "result")
        self.assertEqual(self.google_client.calls[-1], ("read_sheet", "行政", 20))

    def test_google_tool_descriptions_prevent_oauth_fallback(self):
        for schema in (
            kumi_tools.READ_FAMILY_SHEET_SCHEMA,
            kumi_tools.LIST_FAMILY_CALENDAR_SCHEMA,
            kumi_tools.CREATE_FAMILY_CALENDAR_SCHEMA,
        ):
            description = schema["description"]
            self.assertIn("service account", description)
            self.assertIn("do not use terminal", description)
            self.assertIn("Google OAuth", description)

    def test_calendar_write_requires_confirmation(self):
        args = {
            "title": "長照評估",
            "start": "2026-08-28T10:00:00+08:00",
            "end": "2026-08-28T11:00:00+08:00",
            "confirmed": False,
        }
        body = json.loads(kumi_tools.handle_create_family_calendar_event(args))
        self.assertEqual(body["kind"], "error")
        self.assertIn("confirmation_required", body["value"])
        self.assertEqual(self.google_client.calls, [])

    def test_calendar_write_uses_trusted_message_for_idempotency(self):
        args = {
            "title": " 長照評估 ",
            "start": "2026-08-28T10:00:00+08:00",
            "end": "2026-08-28T11:00:00+08:00",
            "confirmed": True,
        }
        first = json.loads(kumi_tools.handle_create_family_calendar_event(args))
        second = json.loads(kumi_tools.handle_create_family_calendar_event(args))
        self.assertEqual(first["kind"], "result")
        first_kwargs = self.google_client.calls[-2][1]
        second_kwargs = self.google_client.calls[-1][1]
        self.assertEqual(first_kwargs["operation_key"], second_kwargs["operation_key"])
        self.assertEqual(first_kwargs["title"], "長照評估")


if __name__ == "__main__":
    unittest.main()
