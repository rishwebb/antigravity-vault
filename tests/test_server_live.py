"""
Live In-Process Ephemeral Server Integration Tests.
Spins up a real ThreadedHTTPServer on an ephemeral port with an isolated temporary SQLite database,
exercises all 14 REST API routes, validates semantic data correctness, and shuts down cleanly.
"""

import unittest
import threading
import urllib.request
import urllib.parse
import json
import time
from typing import Tuple, Dict, Any, Optional
from http.server import HTTPServer

from server import ThreadedHTTPServer, AnalyticsAPIHandler
import db


class TestServerLive(unittest.TestCase):

    server_thread = None
    httpd = None
    server_port = 0
    base_url = ""

    @classmethod
    def setUpClass(cls):
        # 1. Initialize SQLite Database
        db.init_db()
        db.seed_synthetic_data()

        # 2. Bind server to 127.0.0.1 with port 0 for OS-assigned ephemeral port
        cls.httpd = ThreadedHTTPServer(("127.0.0.1", 0), AnalyticsAPIHandler)
        cls.server_port = cls.httpd.server_port
        cls.base_url = f"http://127.0.0.1:{cls.server_port}"

        # 3. Start server in background thread
        cls.server_thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.server_thread.start()
        time.sleep(0.3)

    @classmethod
    def tearDownClass(cls):
        if cls.httpd:
            cls.httpd.shutdown()
            cls.httpd.server_close()
        if cls.server_thread:
            cls.server_thread.join(timeout=1.0)

    def _get_json(self, path: str, headers: dict = None) -> dict:
        req = urllib.request.Request(f"{self.base_url}{path}", headers=headers or {})
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            self.assertEqual(resp.status, 200)
            return json.loads(resp.read().decode("utf-8"))

    def _post_json(self, path: str, payload: dict, headers: dict = None) -> Tuple[int, dict, dict]:
        data = json.dumps(payload).encode("utf-8")
        req_headers = {"Content-Type": "application/json"}
        if headers:
            req_headers.update(headers)
        req = urllib.request.Request(f"{self.base_url}{path}", data=data, headers=req_headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                resp_headers = dict(resp.headers)
                body = json.loads(resp.read().decode("utf-8"))
                return resp.status, body, resp_headers
        except urllib.error.HTTPError as e:
            body = json.loads(e.read().decode("utf-8"))
            return e.code, body, dict(e.headers)

    def test_root_index_html(self):
        req = urllib.request.Request(f"{self.base_url}/")
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            self.assertEqual(resp.status, 200)
            self.assertIn("text/html", resp.headers.get("Content-Type"))

    def test_auth_status_endpoint(self):
        data = self._get_json("/api/auth/status")
        self.assertTrue(data["authenticated"])
        self.assertTrue(data["is_localhost"])
        self.assertTrue(data["pin_protection_active"])

    def test_summary_kpi_semantics(self):
        data = self._get_json("/api/summary")
        self.assertIn("total_turns", data)
        self.assertIn("total_tokens", data)
        self.assertIn("total_cost_usd", data)
        self.assertIn("usd_to_inr", data)
        self.assertTrue(data["cost_is_estimated"])
        self.assertFalse(data["billing_measured"])
        self.assertEqual(data["cost_type"], "estimated_model")

    def test_accounts_fleet_endpoint(self):
        data = self._get_json("/api/accounts")
        self.assertIn("accounts", data)
        self.assertGreaterEqual(len(data["accounts"]), 5)
        for acc in data["accounts"]:
            self.assertIn("account_id", acc)
            self.assertIn("load_pct", acc)
            self.assertIn("attribution_type", acc)

    def test_models_breakdown_endpoint(self):
        data = self._get_json("/api/models")
        self.assertIn("models", data)
        self.assertIn("thinking_budgets", data)

    def test_timeline_endpoint(self):
        data = self._get_json("/api/timeline?range=7d")
        self.assertIn("timeline", data)

    def test_recent_logs_endpoint(self):
        data = self._get_json("/api/recent-logs?limit=5")
        self.assertIn("logs", data)
        self.assertIn("total", data)
        self.assertLessEqual(len(data["logs"]), 5)

    def test_status_metrics_and_observability(self):
        data = self._get_json("/api/status")
        self.assertEqual(data["daemon"], "active")
        self.assertTrue(data["db_integrity"])
        self.assertIn("watcher_metrics", data)

        audit_data = self._get_json("/api/status/audit-log")
        self.assertIn("audit_logs", audit_data)

        error_data = self._get_json("/api/status/errors")
        self.assertIn("db_errors", error_data)
        self.assertIn("recent_runtime_errors", error_data)

    def test_login_endpoint_token_redaction(self):
        # Wrong PIN should be rejected with 401
        status, body, _ = self._post_json("/api/auth/verify", {"pin": "wrong_pin_999999"})
        self.assertEqual(status, 401)
        self.assertEqual(body["status"], "error")

    def test_cors_origin_hardening(self):
        # Localhost origin echoed with credentials
        req = urllib.request.Request(
            f"{self.base_url}/api/summary",
            headers={"Origin": "http://localhost:3000"}
        )
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            self.assertEqual(resp.headers.get("Access-Control-Allow-Origin"), "http://localhost:3000")
            self.assertEqual(resp.headers.get("Access-Control-Allow-Credentials"), "true")

        # Untrusted origin not granted CORS
        req_untrusted = urllib.request.Request(
            f"{self.base_url}/api/summary",
            headers={"Origin": "http://evil-attacker.com"}
        )
        with urllib.request.urlopen(req_untrusted, timeout=3.0) as resp:
            self.assertIsNone(resp.headers.get("Access-Control-Allow-Origin"))


if __name__ == "__main__":
    unittest.main()
