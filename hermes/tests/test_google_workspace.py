import os
import sys
import unittest
from datetime import date
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

import httpx  # noqa: E402

from kumi.client import KumiConfigError, KumiUpstreamError  # noqa: E402
from kumi.google_workspace import (  # noqa: E402
    GoogleWorkspaceClient,
    GoogleWorkspaceConfig,
    calendar_event_id,
)


class GoogleWorkspaceConfigTest(unittest.TestCase):
    def test_config_parses_allowlist_and_defaults_to_read_only(self):
        original = dict(os.environ)
        try:
            os.environ.update({
                "KUMI_GOOGLE_SERVICE_ACCOUNT_FILE": "/secure/google.json",
                "KUMI_GOOGLE_SPREADSHEET_ID": "sheet-id",
                "KUMI_GOOGLE_CALENDAR_ID": "calendar-id",
                "KUMI_GOOGLE_ALLOWED_SHEETS": "注意事項, 療程,行政",
            })
            os.environ.pop("KUMI_GOOGLE_MUTATIONS_ENABLED", None)
            config = GoogleWorkspaceConfig.from_env()
            self.assertEqual(config.allowed_sheets, ("注意事項", "療程", "行政"))
            self.assertEqual(config.timezone, "Asia/Taipei")
            self.assertFalse(config.mutations_enabled)
        finally:
            os.environ.clear()
            os.environ.update(original)

    def test_config_requires_absolute_credential_path(self):
        original = dict(os.environ)
        try:
            os.environ.update({
                "KUMI_GOOGLE_SERVICE_ACCOUNT_FILE": "google.json",
                "KUMI_GOOGLE_SPREADSHEET_ID": "sheet-id",
                "KUMI_GOOGLE_CALENDAR_ID": "calendar-id",
                "KUMI_GOOGLE_ALLOWED_SHEETS": "行政",
            })
            with self.assertRaises(KumiConfigError):
                GoogleWorkspaceConfig.from_env()
        finally:
            os.environ.clear()
            os.environ.update(original)


class GoogleWorkspaceClientTest(unittest.TestCase):
    def config(self, *, mutations_enabled=False):
        return GoogleWorkspaceConfig(
            service_account_file="/secure/google.json",
            spreadsheet_id="sheet-id",
            calendar_id="calendar-id@example.com",
            allowed_sheets=("療程", "行政"),
            timezone="Asia/Taipei",
            mutations_enabled=mutations_enabled,
        )

    def test_sheet_read_is_fixed_to_allowlisted_sheet_and_row_limit(self):
        requests = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, json={"range": "療程!A1:C2", "values": [["階段", "時程"], ["門診", "2026/8/28"]]})

        session = httpx.Client(transport=httpx.MockTransport(handler))
        client = GoogleWorkspaceClient(self.config(), session=session)
        try:
            result = client.read_sheet("療程", max_rows=20)
            self.assertTrue(result["ok"])
            self.assertEqual(result["data"]["row_count"], 2)
            self.assertIn("%27%E7%99%82%E7%A8%8B%27%21A1%3AZ20", str(requests[0].url))
            with self.assertRaisesRegex(ValueError, "sheet_not_allowed"):
                client.read_sheet("銀行帳戶")
        finally:
            client.close()

    def test_calendar_read_uses_configured_calendar_and_timezone(self):
        requests = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, json={"items": [{
                "id": "event-1",
                "summary": "家庭行程",
                "status": "confirmed",
                "start": {"dateTime": "2026-08-28T10:00:00+08:00"},
                "end": {"dateTime": "2026-08-28T11:00:00+08:00"},
            }]})

        session = httpx.Client(transport=httpx.MockTransport(handler))
        client = GoogleWorkspaceClient(self.config(), session=session)
        try:
            result = client.list_calendar_events(date(2026, 8, 1), date(2026, 9, 1))
            self.assertEqual(result["data"][0]["title"], "家庭行程")
            self.assertIn("calendar-id%40example.com/events", str(requests[0].url))
            self.assertIn("timeZone=Asia%2FTaipei", str(requests[0].url))
        finally:
            client.close()

    def test_calendar_write_is_idempotent_and_requires_mutations(self):
        read_only = GoogleWorkspaceClient(self.config(), session=httpx.Client())
        with self.assertRaises(KumiConfigError):
            read_only.create_calendar_event(
                operation_key="v1:abc",
                title="家庭行程",
                start="2026-08-28T10:00:00+08:00",
                end="2026-08-28T11:00:00+08:00",
                location=None,
                description=None,
            )
        read_only.close()

        captured = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            body = {
                "id": calendar_event_id("v1:abc"),
                "summary": "家庭行程",
                "start": {"dateTime": "2026-08-28T10:00:00+08:00"},
                "end": {"dateTime": "2026-08-28T11:00:00+08:00"},
            }
            return httpx.Response(200, json=body)

        session = httpx.Client(transport=httpx.MockTransport(handler))
        client = GoogleWorkspaceClient(self.config(mutations_enabled=True), session=session)
        try:
            result = client.create_calendar_event(
                operation_key="v1:abc",
                title="家庭行程",
                start="2026-08-28T10:00:00+08:00",
                end="2026-08-28T11:00:00+08:00",
                location=None,
                description=None,
            )
            self.assertTrue(result["ok"])
            self.assertEqual(captured[0].method, "POST")
            self.assertNotRegex(calendar_event_id("v1:abc"), r"[w-z]")
        finally:
            client.close()

    def test_upstream_error_does_not_expose_response_body(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(403, text="private Google error detail")

        session = httpx.Client(transport=httpx.MockTransport(handler))
        client = GoogleWorkspaceClient(self.config(), session=session)
        try:
            with self.assertRaisesRegex(KumiUpstreamError, r"sheet read failed \(403\)") as caught:
                client.read_sheet("行政")
            self.assertNotIn("private Google error detail", str(caught.exception))
        finally:
            client.close()


if __name__ == "__main__":
    unittest.main()
