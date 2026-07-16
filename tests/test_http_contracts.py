from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "weber_connect_ble" / "app"
sys.path.insert(0, str(APP))

from weber_http import MAX_REQUEST_BODY, create_panel_server  # noqa: E402

INGRESS_HEADERS = {"Content-Type": "application/json", "X-Ingress-Path": "/api/hassio_ingress/token"}


class FakeController:
    def __init__(self) -> None:
        self.settings = {}

    def heartbeat(self):
        return {"ok": True, "state": "online", "loop_beat": "2026-01-01T00:00:00+00:00"}

    async def snapshot(self):
        return {"state": "online", "version": "test"}

    async def update_settings(self, payload):
        self.settings.update(payload)
        return {"ok": True, "settings": dict(self.settings)}

    async def start_scan(self):
        return {"ok": True}

    async def pair(self, _address):
        return {"ok": True}

    async def handoff(self, _minutes):
        return {"ok": True}

    async def resume(self):
        return {"ok": True}

    async def forget(self):
        return {"ok": True}


class HttpContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        temporary = Path(self._tmp.name)
        self.index = temporary / "index.html"
        self.icon = temporary / "icon.png"
        self.index.write_text("<!doctype html><title>test</title>", encoding="utf-8")
        self.icon.write_bytes(b"\x89PNG\r\n\x1a\n")
        self.loop = asyncio.new_event_loop()
        self.loop_thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.loop_thread.start()
        self.controller = FakeController()
        self.server = create_panel_server(
            controller=self.controller,
            loop=self.loop,
            port=0,
            index_file=self.index,
            icon_file=self.icon,
        )
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.server_thread.join(timeout=2)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.loop_thread.join(timeout=2)
        self.loop.close()
        self._tmp.cleanup()

    def test_status_and_security_headers(self) -> None:
        with urllib.request.urlopen(f"{self.base}/api/status", timeout=2) as response:
            payload = json.loads(response.read())
            self.assertEqual(payload["state"], "online")
            self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
            self.assertIn("default-src 'self'", response.headers["Content-Security-Policy"])
            self.assertNotIn("Python", response.headers["Server"])

    def test_static_panel_and_icon_are_served_with_correct_cache_policy(self) -> None:
        with urllib.request.urlopen(f"{self.base}/", timeout=2) as response:
            self.assertEqual(response.headers.get_content_type(), "text/html")
            self.assertEqual(response.headers["Cache-Control"], "no-store")
        with urllib.request.urlopen(f"{self.base}/icon.png", timeout=2) as response:
            self.assertEqual(response.headers.get_content_type(), "image/png")
            self.assertIn("immutable", response.headers["Cache-Control"])

    def test_head_matches_static_get_without_a_body(self) -> None:
        for path, content_type in (
            ("/", "text/html"),
            ("/icon.png", "image/png"),
            ("/api/status", "application/json"),
        ):
            request = urllib.request.Request(f"{self.base}{path}", method="HEAD")
            with urllib.request.urlopen(request, timeout=2) as response:
                self.assertEqual(response.headers.get_content_type(), content_type)
                self.assertEqual(response.read(), b"")
                self.assertGreater(int(response.headers["Content-Length"]), 0)

    def test_health_endpoint_is_open_and_lock_free(self) -> None:
        with urllib.request.urlopen(f"{self.base}/api/health", timeout=2) as response:
            payload = json.loads(response.read())
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["state"], "online")

    def test_mutations_require_ingress_provenance(self) -> None:
        blocked = urllib.request.Request(
            f"{self.base}/api/scan",
            data=json.dumps({}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(blocked, timeout=2)
        self.assertEqual(error.exception.code, 403)
        error.exception.close()

    def test_json_action_round_trip(self) -> None:
        request = urllib.request.Request(
            f"{self.base}/api/settings",
            data=json.dumps({"poll_seconds": 60}).encode(),
            headers=dict(INGRESS_HEADERS),
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=2) as response:
            payload = json.loads(response.read())
        self.assertTrue(payload["ok"])
        self.assertEqual(self.controller.settings["poll_seconds"], 60)

    def test_rejects_wrong_content_type_and_oversized_body(self) -> None:
        wrong_type = urllib.request.Request(
            f"{self.base}/api/settings",
            data=b"{}",
            headers={"X-Ingress-Path": "/api/hassio_ingress/token"},
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as wrong_error:
            urllib.request.urlopen(wrong_type, timeout=2)
        self.assertEqual(wrong_error.exception.code, 415)
        wrong_error.exception.close()

        oversized = urllib.request.Request(
            f"{self.base}/api/settings",
            data=b"x" * (MAX_REQUEST_BODY + 1),
            headers=dict(INGRESS_HEADERS),
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as large_error:
            urllib.request.urlopen(oversized, timeout=2)
        self.assertEqual(large_error.exception.code, 413)
        large_error.exception.close()

    def test_unknown_action_and_non_object_json_are_rejected(self) -> None:
        for path, body, expected in (
            ("unknown", {}, 404),
            ("settings", [], 400),
        ):
            request = urllib.request.Request(
                f"{self.base}/api/{path}",
                data=json.dumps(body).encode(),
                headers=dict(INGRESS_HEADERS),
                method="POST",
            )
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(request, timeout=2)
            self.assertEqual(error.exception.code, expected)
            error.exception.close()


if __name__ == "__main__":
    unittest.main()
