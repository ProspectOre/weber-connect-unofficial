"""Small hardened HTTP adapter for the Home Assistant ingress panel."""

from __future__ import annotations

import asyncio
import json
import logging
from concurrent.futures import TimeoutError as FutureTimeoutError
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger("weber_connect_http")
MAX_REQUEST_BODY = 16 * 1024
ACTION_TIMEOUT = 30.0
READ_TIMEOUT = 5.0
# Supervisor proxies every ingress request from this fixed address and stamps
# the ingress headers it injects. Mutating actions require that provenance so a
# neighbouring container on the Supervisor network cannot drive the hub.
SUPERVISOR_INGRESS_IP = "172.30.32.2"
INGRESS_HEADERS = ("X-Ingress-Path", "X-Remote-User", "X-Hass-User-Id")


class PanelHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class PanelRequestHandler(BaseHTTPRequestHandler):
    controller: Any = None
    loop: asyncio.AbstractEventLoop | None = None
    index_file: Path | None = None
    icon_file: Path | None = None
    server_version = "WeberConnectPanel/1"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(ACTION_TIMEOUT + 5)

    def log_message(self, format: str, *args: Any) -> None:
        LOGGER.debug("http: " + format, *args)

    def _call(self, coroutine: Any, *, timeout: float) -> Any:
        if self.loop is None:
            raise RuntimeError("panel event loop is unavailable")
        future = asyncio.run_coroutine_threadsafe(coroutine, self.loop)
        try:
            return future.result(timeout=timeout)
        except FutureTimeoutError:
            future.cancel()
            raise

    def _send_security_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
            "connect-src 'self'; frame-ancestors 'self'",
        )

    def _send_bytes(
        self,
        body: bytes,
        *,
        content_type: str,
        status: int = 200,
        cache_control: str = "no-store",
        head_only: bool = False,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", cache_control)
        self._send_security_headers()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if not head_only:
            self.wfile.write(body)

    def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        self._send_bytes(
            json.dumps(payload).encode("utf-8"),
            content_type="application/json",
            status=status,
        )

    def _route(self) -> str:
        return self.path.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1]

    def _is_ingress_request(self) -> bool:
        if any(self.headers.get(header) for header in INGRESS_HEADERS):
            return True
        return self.client_address and self.client_address[0] == SUPERVISOR_INGRESS_IP

    def do_GET(self) -> None:
        route = self._route()
        if route == "health":
            # Liveness probe for the Supervisor watchdog. It must never take a
            # panel lock or hop onto the event loop, so a wedged bridge loop is
            # reported as unhealthy instead of hanging the probe.
            self._send_json(self.controller.heartbeat())
            return
        if route == "status":
            try:
                payload = self._call(self.controller.snapshot(), timeout=READ_TIMEOUT)
            except FutureTimeoutError:
                self._send_json({"ok": False, "error": "status request timed out"}, status=504)
                return
            except Exception as exc:
                LOGGER.error("Status request failed", exc_info=True)
                self._send_json({"ok": False, "error": str(exc)}, status=500)
                return
            self._send_json(payload)
            return
        if route == "icon.png" and self.icon_file and self.icon_file.is_file():
            self._send_bytes(
                self.icon_file.read_bytes(),
                content_type="image/png",
                cache_control="public, max-age=86400, immutable",
            )
            return
        if self.index_file and self.index_file.is_file():
            self._send_bytes(
                self.index_file.read_bytes(),
                content_type="text/html; charset=utf-8",
            )
            return
        self._send_json({"ok": False, "error": "panel UI is missing"}, status=404)

    def do_HEAD(self) -> None:
        route = self._route()
        if route == "status":
            try:
                payload = self._call(self.controller.snapshot(), timeout=READ_TIMEOUT)
            except FutureTimeoutError:
                body = json.dumps(
                    {"ok": False, "error": "status request timed out"}
                ).encode("utf-8")
                self._send_bytes(
                    body,
                    content_type="application/json",
                    status=504,
                    head_only=True,
                )
                return
            except Exception as exc:
                LOGGER.error("Status request failed", exc_info=True)
                body = json.dumps({"ok": False, "error": str(exc)}).encode("utf-8")
                self._send_bytes(
                    body,
                    content_type="application/json",
                    status=500,
                    head_only=True,
                )
                return
            body = json.dumps(payload).encode("utf-8")
            self._send_bytes(body, content_type="application/json", head_only=True)
            return
        if route == "icon.png" and self.icon_file and self.icon_file.is_file():
            self._send_bytes(
                self.icon_file.read_bytes(),
                content_type="image/png",
                cache_control="public, max-age=86400, immutable",
                head_only=True,
            )
            return
        if self.index_file and self.index_file.is_file():
            self._send_bytes(
                self.index_file.read_bytes(),
                content_type="text/html; charset=utf-8",
                head_only=True,
            )
            return
        self._send_bytes(
            json.dumps({"ok": False, "error": "panel UI is missing"}).encode("utf-8"),
            content_type="application/json",
            status=404,
            head_only=True,
        )

    def do_POST(self) -> None:
        if not self._is_ingress_request():
            self._send_json(
                {"ok": False, "error": "requests must arrive through Home Assistant ingress"},
                status=403,
            )
            return
        content_type = self.headers.get_content_type()
        if content_type != "application/json":
            self._send_json(
                {"ok": False, "error": "Content-Type must be application/json"},
                status=415,
            )
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self._send_json({"ok": False, "error": "invalid Content-Length"}, status=400)
            return
        if length < 0 or length > MAX_REQUEST_BODY:
            self._send_json({"ok": False, "error": "request body is too large"}, status=413)
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
        except (UnicodeDecodeError, ValueError):
            self._send_json({"ok": False, "error": "invalid JSON body"}, status=400)
            return
        if not isinstance(payload, dict):
            self._send_json({"ok": False, "error": "JSON body must be an object"}, status=400)
            return

        actions = {
            "scan": lambda: self.controller.start_scan(),
            "pair": lambda: self.controller.pair(payload.get("address")),
            "handoff": lambda: self.controller.handoff(payload.get("minutes")),
            "resume": lambda: self.controller.resume(),
            "forget": lambda: self.controller.forget(),
            "settings": lambda: self.controller.update_settings(payload),
        }
        action = actions.get(self._route())
        if action is None:
            self._send_json({"ok": False, "error": "unknown action"}, status=404)
            return
        try:
            result = self._call(action(), timeout=ACTION_TIMEOUT)
        except FutureTimeoutError:
            self._send_json({"ok": False, "error": "action timed out"}, status=504)
            return
        except Exception as exc:
            LOGGER.error("Action %s failed", self._route(), exc_info=True)
            self._send_json({"ok": False, "error": str(exc)}, status=500)
            return
        self._send_json(result, status=200 if result.get("ok") else 400)

    def do_OPTIONS(self) -> None:
        self._send_json({"ok": False, "error": "method not allowed"}, status=405)


def create_panel_server(
    *,
    controller: Any,
    loop: asyncio.AbstractEventLoop,
    port: int,
    index_file: Path,
    icon_file: Path,
) -> PanelHTTPServer:
    handler = type(
        "BoundPanelRequestHandler",
        (PanelRequestHandler,),
        {
            "controller": controller,
            "loop": loop,
            "index_file": index_file,
            "icon_file": icon_file,
        },
    )
    return PanelHTTPServer(("0.0.0.0", port), handler)
