import json
import os
import sys
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from kumi.client import (  # noqa: E402
    KumiConfig,
    KumiConfigError,
    SupabaseRpcClient,
    canonical_payload,
    normalize_member_name,
    operation_key,
    payload_hash,
)

import httpx  # noqa: E402


class ClientHelpersTest(unittest.TestCase):
    def test_canonical_payload_is_stable_and_omits_nulls(self):
        left = {"title": "帶媽媽回診", "due_date": None, "version": 1}
        right = {"version": 1, "title": "帶媽媽回診"}
        self.assertEqual(canonical_payload(left), canonical_payload(right))
        self.assertEqual(payload_hash(left), payload_hash(right))

    def test_member_name_normalization(self):
        self.assertEqual(normalize_member_name("  JaCEE  Lin "), "jacee lin")

    def test_operation_key_is_context_bound(self):
        first = operation_key("line", "group-a", "message-1", "create_family_task")
        second = operation_key("line", "group-b", "message-1", "create_family_task")
        self.assertNotEqual(first, second)
        self.assertEqual(first, operation_key("line", "group-a", "message-1", "create_family_task"))

    def test_config_fails_closed(self):
        original = dict(os.environ)
        try:
            for key in (
                "KUMI_SUPABASE_URL",
                "KUMI_SUPABASE_PUBLISHABLE_KEY",
                "KUMI_SUPABASE_EMAIL",
                "KUMI_SUPABASE_PASSWORD",
            ):
                os.environ.pop(key, None)
            with self.assertRaises(KumiConfigError):
                KumiConfig.from_env()
        finally:
            os.environ.clear()
            os.environ.update(original)


class AuthClientTest(unittest.TestCase):
    def test_password_login_and_rpc(self):
        requests = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.url.path == "/auth/v1/token":
                return httpx.Response(200, json={
                    "access_token": "access",
                    "refresh_token": "refresh",
                    "expires_in": 3600,
                })
            self.assertEqual(request.headers["authorization"], "Bearer access")
            self.assertEqual(request.headers["content-profile"], "kumi_core")
            return httpx.Response(200, json={"ok": True, "data": []})

        config = KumiConfig("https://example.supabase.co", "publishable", "agent@example.com", "secret", False)
        client = SupabaseRpcClient(config, transport=httpx.MockTransport(handler))
        try:
            body = client.rpc("list_family_tasks", {"p_scope": "mine"})
            self.assertTrue(body["ok"])
            self.assertEqual(len(requests), 2)
        finally:
            client.close()

    def test_error_does_not_include_upstream_body(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, text="sensitive upstream detail")

        config = KumiConfig("https://example.supabase.co", "publishable", "agent@example.com", "secret", False)
        client = SupabaseRpcClient(config, transport=httpx.MockTransport(handler))
        with self.assertRaisesRegex(Exception, r"login failed \(401\)") as caught:
            client.access_token()
        self.assertNotIn("sensitive upstream detail", str(caught.exception))
        client.close()


if __name__ == "__main__":
    unittest.main()
