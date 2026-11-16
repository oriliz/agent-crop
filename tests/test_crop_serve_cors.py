"""Cross-origin rules for the local dashboard.

The dashboard serves numbers about the user's working day from 127.0.0.1. A
wildcard CORS header would let any page in the browser read them, so only
loopback origins are echoed back. These tests pin that contract.
"""

import http.client
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "core"))
import crop_serve  # noqa: E402


class CorsHeaderTest(unittest.TestCase):
    """Run a real server on an ephemeral port and talk HTTP to it."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls._env = {}
        for key in ("KIT_SHARED_HOME", "KIT_DATA_DIR"):
            cls._env[key] = os.environ.get(key)
            os.environ[key] = cls._tmp.name
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), crop_serve.Handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        for key, value in cls._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        cls._tmp.cleanup()

    def _get(self, path, origin=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {"Origin": origin} if origin is not None else {}
        connection.request("GET", path, headers=headers)
        response = connection.getresponse()
        response.read()
        connection.close()
        return response

    def test_page_still_served(self):
        response = self._get("/")
        self.assertEqual(response.status, 200)
        self.assertIn("text/html", response.getheader("Content-Type"))

    def test_loopback_origin_is_echoed(self):
        for origin in ("http://127.0.0.1:3082", "http://localhost:8765", "http://127.0.0.1"):
            with self.subTest(origin=origin):
                response = self._get("/api/summary?days=1", origin=origin)
                self.assertEqual(response.status, 200)
                self.assertEqual(response.getheader("Access-Control-Allow-Origin"), origin)
                self.assertEqual(response.getheader("Vary"), "Origin")

    def test_foreign_origin_gets_no_cors_header(self):
        for origin in ("https://evil.example", "http://127.0.0.1.evil.example", "null"):
            with self.subTest(origin=origin):
                response = self._get("/api/summary?days=1", origin=origin)
                self.assertEqual(response.status, 200)
                self.assertIsNone(response.getheader("Access-Control-Allow-Origin"))

    def test_no_origin_sends_no_cors_header(self):
        response = self._get("/api/summary?days=1")
        self.assertEqual(response.status, 200)
        self.assertIsNone(response.getheader("Access-Control-Allow-Origin"))

    def test_all_json_endpoints_are_protected(self):
        for path in ("/api/summary?days=1", "/api/session", "/api/day?date=2026-11-15",
                     "/api/tool-health?days=7", "/api/health"):
            with self.subTest(path=path):
                self.assertIsNone(self._get(path, origin="https://evil.example").getheader("Access-Control-Allow-Origin"))
                self.assertIsNotNone(self._get(path, origin="http://127.0.0.1:3082").getheader("Access-Control-Allow-Origin"))


    def test_tool_health_payload_shape(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        connection.request("GET", "/api/tool-health?days=7")
        response = connection.getresponse()
        payload = json.loads(response.read())
        connection.close()
        self.assertEqual(response.status, 200)
        for key in ("generated_at", "window", "sessions", "passive", "active", "diagnostics", "summary"):
            self.assertIn(key, payload)
        self.assertTrue(any(row["tool"] == "tool" for row in payload["passive"]))
        self.assertTrue(any(row["tool"] == "probe_report" for row in payload["active"]))

    def test_process_health_identity_is_not_the_health_tab(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        connection.request("GET", "/api/health")
        response = connection.getresponse()
        payload = json.loads(response.read())
        connection.close()
        self.assertEqual(payload["ok"], True)
        self.assertIn("pid", payload)
        self.assertNotIn("passive", payload)


if __name__ == "__main__":
    unittest.main()
