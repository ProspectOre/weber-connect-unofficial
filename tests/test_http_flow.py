from __future__ import annotations

import asyncio
import io
import json
import sys
import threading
import unittest
from http.client import HTTPMessage
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "weber_connect_ble" / "app"
sys.path.insert(0, str(APP))

import weber_http as weber_http  # noqa: E402


def make_headers(mapping: dict[str, str]) -> HTTPMessage:
    message = HTTPMessage()
    for key, value in mapping.items():
        message[key] = value
    return message


class DirectHandler(weber_http.PanelRequestHandler):
    """A handler exercised without a socket by calling do_* directly."""

    def __init__(
        self,
        *,
        controller=None,
        loop=None,
        path="/",
        headers=None,
        body=b"",
        index_file=None,
        icon_file=None,
        client_ip="172.30.32.2",
    ) -> None:
        self.controller = controller
        self.loop = loop
        self.path = path
        self.index_file = index_file
        self.icon_file = icon_file
        self.client_address = (client_ip, 1234)
        self.headers = make_headers(headers or {})
        self.rfile = io.BytesIO(body)
        self.wfile = io.BytesIO()
        self.request_version = "HTTP/1.1"
        self.command = ""
        self.status: int | None = None

    def send_response(self, code, message=None) -> None:
        self.status = code

    def send_header(self, key, value) -> None:
        pass

    def end_headers(self) -> None:
        pass

    def log_message(self, *args, **kwargs) -> None:
        pass

    def json_body(self) -> dict:
        return json.loads(self.wfile.getvalue().decode("utf-8"))


class Controller:
    def __init__(self) -> None:
        self.snapshot_impl = None
        self.actions: dict = {}

    def heartbeat(self):
        return {"ok": True, "state": "online"}

    async def snapshot(self):
        return await self.snapshot_impl()

    def __getattr__(self, name):
        async def call(*args, **kwargs):
            return await self.actions[name](*args, **kwargs)

        return call


class HttpFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.loop = asyncio.new_event_loop()
        cls.loop_thread = threading.Thread(target=cls.loop.run_forever, daemon=True)
        cls.loop_thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.loop.call_soon_threadsafe(cls.loop.stop)
        cls.loop_thread.join(timeout=2)
        cls.loop.close()

    def test_call_without_loop_raises(self) -> None:
        handler = DirectHandler(controller=Controller(), loop=None, path="/api/status")
        handler.do_GET()
        self.assertEqual(handler.status, 500)

    def test_status_timeout_returns_504(self) -> None:
        controller = Controller()

        async def hang():
            await asyncio.sleep(10)

        controller.snapshot_impl = hang
        handler = DirectHandler(
            controller=controller, loop=self.loop, path="/api/status"
        )
        with mock.patch.object(weber_http, "READ_TIMEOUT", 0.1):
            handler.do_GET()
        self.assertEqual(handler.status, 504)
        self.assertFalse(handler.json_body()["ok"])

    def test_status_exception_returns_500(self) -> None:
        controller = Controller()

        async def boom():
            raise RuntimeError("nope")

        controller.snapshot_impl = boom
        handler = DirectHandler(
            controller=controller, loop=self.loop, path="/api/status"
        )
        with self.assertLogs("weber_connect_http", level="ERROR"):
            handler.do_GET()
        self.assertEqual(handler.status, 500)

    def test_head_status_success(self) -> None:
        controller = Controller()

        async def snap():
            return {"state": "online"}

        controller.snapshot_impl = snap
        handler = DirectHandler(
            controller=controller, loop=self.loop, path="/api/status"
        )
        handler.do_HEAD()
        self.assertEqual(handler.status, 200)
        self.assertEqual(handler.wfile.getvalue(), b"")

    def test_head_status_timeout_returns_504(self) -> None:
        controller = Controller()

        async def hang():
            await asyncio.sleep(10)

        controller.snapshot_impl = hang
        handler = DirectHandler(
            controller=controller, loop=self.loop, path="/api/status"
        )
        with mock.patch.object(weber_http, "READ_TIMEOUT", 0.1):
            handler.do_HEAD()
        self.assertEqual(handler.status, 504)

    def test_head_status_exception_returns_500(self) -> None:
        controller = Controller()

        async def boom():
            raise RuntimeError("nope")

        controller.snapshot_impl = boom
        handler = DirectHandler(
            controller=controller, loop=self.loop, path="/api/status"
        )
        with self.assertLogs("weber_connect_http", level="ERROR"):
            handler.do_HEAD()
        self.assertEqual(handler.status, 500)

    def test_head_missing_panel_returns_404(self) -> None:
        handler = DirectHandler(
            controller=Controller(), loop=self.loop, path="/", index_file=None
        )
        handler.do_HEAD()
        self.assertEqual(handler.status, 404)

    def test_post_invalid_content_length_returns_400(self) -> None:
        controller = Controller()
        handler = DirectHandler(
            controller=controller,
            loop=self.loop,
            path="/api/scan",
            headers={"Content-Type": "application/json", "Content-Length": "abc"},
        )
        handler.do_POST()
        self.assertEqual(handler.status, 400)

    def test_post_invalid_json_returns_400(self) -> None:
        controller = Controller()
        body = b"{not json"
        handler = DirectHandler(
            controller=controller,
            loop=self.loop,
            path="/api/scan",
            headers={
                "Content-Type": "application/json",
                "Content-Length": str(len(body)),
            },
            body=body,
        )
        handler.do_POST()
        self.assertEqual(handler.status, 400)

    def test_post_action_timeout_returns_504(self) -> None:
        controller = Controller()

        async def hang():
            await asyncio.sleep(10)

        controller.actions["start_scan"] = hang
        handler = DirectHandler(
            controller=controller,
            loop=self.loop,
            path="/api/scan",
            headers={"Content-Type": "application/json"},
        )
        with mock.patch.object(weber_http, "ACTION_TIMEOUT", 0.1):
            handler.do_POST()
        self.assertEqual(handler.status, 504)

    def test_post_action_exception_returns_500(self) -> None:
        controller = Controller()

        async def boom():
            raise RuntimeError("nope")

        controller.actions["start_scan"] = boom
        handler = DirectHandler(
            controller=controller,
            loop=self.loop,
            path="/api/scan",
            headers={"Content-Type": "application/json"},
        )
        with self.assertLogs("weber_connect_http", level="ERROR"):
            handler.do_POST()
        self.assertEqual(handler.status, 500)

    def test_post_failed_action_result_returns_400(self) -> None:
        controller = Controller()

        async def refuse():
            return {"ok": False, "error": "bad"}

        controller.actions["start_scan"] = refuse
        handler = DirectHandler(
            controller=controller,
            loop=self.loop,
            path="/api/scan",
            headers={"Content-Type": "application/json"},
        )
        handler.do_POST()
        self.assertEqual(handler.status, 400)

    def test_options_is_method_not_allowed(self) -> None:
        handler = DirectHandler(controller=Controller(), loop=self.loop)
        handler.do_OPTIONS()
        self.assertEqual(handler.status, 405)


if __name__ == "__main__":
    unittest.main()
