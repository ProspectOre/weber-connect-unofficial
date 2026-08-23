"""Minimal read-only Weber companion registration and authentication client."""

from __future__ import annotations

import gzip
import io
import json
import re
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from dataclasses import dataclass
from typing import Any

API_HOST = "api.walker-cloud.com"
MESSAGING_HOST = "messaging.walker-cloud.com"
USER_AGENT = "okhttp/5.3.0"
MAX_RESPONSE_BYTES = 1024 * 1024

# Application credentials identify the Weber Android client, not an account.
APP_CLIENT_ID = "qyw4CGeb/i93BrA0KAUuGtPyKImr+nUKc8lHxFdt"
APP_CLIENT_SECRET = "ekEHLyHw+Ru3H25mH4a9f2OKCMILnMx+YSN2dFIB2zB0PP8NGAnSPTw"  # nosec B105

HEX_ID_RE = re.compile(r"^[0-9a-f]{32}$")


class WeberCloudError(RuntimeError):
    """Cloud transport or response failure."""


class WeberCloudAuthError(WeberCloudError):
    """Companion credentials were rejected."""


def _https_origin(url: str) -> tuple[str, int]:
    """Return a normalized HTTPS origin or reject an unsafe cloud URL."""

    try:
        parsed = urllib.parse.urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise WeberCloudError("Refused a malformed Weber cloud URL.") from exc
    host = (parsed.hostname or "").lower()
    if (
        parsed.scheme.lower() != "https"
        or parsed.username is not None
        or parsed.password is not None
        or host not in {API_HOST, MESSAGING_HOST}
        or port not in {None, 443}
    ):
        raise WeberCloudError("Refused an untrusted Weber cloud URL.")
    return host, 443


class _SameOriginRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Keep bearer-bearing requests on their original HTTPS origin."""

    def redirect_request(  # type: ignore[no-untyped-def]
        self, req, fp, code, msg, headers, newurl
    ) -> urllib.request.Request:
        if _https_origin(req.full_url) != _https_origin(newurl):
            raise WeberCloudError("Refused a cross-origin Weber cloud redirect.")
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is None:
            raise WeberCloudError("Refused an unsupported Weber cloud redirect.")
        return redirected


@dataclass(frozen=True, slots=True)
class CloudConfig:
    """Generated companion credentials stored in a Home Assistant entry."""

    device_id: str
    device_password: str
    appliance_id: str | None = None

    @classmethod
    def from_mapping(cls, payload: dict[str, Any]) -> CloudConfig:
        device_id = str(payload.get("device_id") or "").replace(":", "").strip().lower()
        password = str(payload.get("device_password") or "").strip()
        if not HEX_ID_RE.fullmatch(device_id):
            raise ValueError("Cloud device ID must be 32 hexadecimal characters.")
        if not password or len(password) > 256:
            raise ValueError(
                "Cloud device password is required and must be at most 256 characters."
            )
        raw_appliance_id = payload.get("appliance_id")
        appliance_id = (
            str(raw_appliance_id).replace(":", "").strip().lower() if raw_appliance_id else None
        )
        if appliance_id is not None and not HEX_ID_RE.fullmatch(appliance_id):
            raise ValueError("Cloud appliance ID must be 32 hexadecimal characters.")
        return cls(device_id, password, appliance_id)

    @classmethod
    def generate(cls, companion_id: str) -> CloudConfig:
        device_id = companion_id.replace(":", "").replace("-", "").strip().lower()
        if not HEX_ID_RE.fullmatch(device_id):
            device_id = secrets.token_hex(16)
        return cls(device_id, secrets.token_hex(16))


def resolve_associated_appliance_id(
    appliances: list[dict[str, Any]],
    expected_appliance_id: str | None = None,
) -> str | None:
    """Resolve one associated hub without selecting an unrelated appliance."""

    candidates: list[str] = []
    for appliance in appliances:
        for key in ("oven_id", "appliance_id", "device_id", "id"):
            value = appliance.get(key)
            if not isinstance(value, str):
                continue
            normalized = value.replace(":", "").strip().lower()
            if HEX_ID_RE.fullmatch(normalized) and normalized not in candidates:
                candidates.append(normalized)
    if expected_appliance_id:
        expected = expected_appliance_id.replace(":", "").strip().lower()
        if expected in candidates:
            return expected
    return candidates[0] if len(candidates) == 1 else None


class WeberCloudClient:
    """Synchronous HTTPS client used from Home Assistant's executor."""

    def __init__(self, config: CloudConfig, *, timeout: float = 20.0) -> None:
        self.config = config
        self.timeout = timeout
        self._token: str | None = None
        self._token_expiry = 0.0
        self._opener = urllib.request.build_opener(_SameOriginRedirectHandler())

    @property
    def messaging_host(self) -> str:
        return MESSAGING_HOST

    @property
    def user_agent(self) -> str:
        return USER_AGENT

    def close(self) -> None:
        """Discard the bearer token when the config entry unloads."""

        self._token = None
        self._token_expiry = 0.0

    def _open(self, request: urllib.request.Request) -> bytes:
        _https_origin(request.full_url)
        try:
            with self._opener.open(request, timeout=self.timeout) as response:  # nosec B310
                body = bytes(response.read(MAX_RESPONSE_BYTES + 1))
                if len(body) > MAX_RESPONSE_BYTES:
                    raise WeberCloudError("Weber cloud returned an oversized response.")
                if response.headers.get("Content-Encoding", "").lower() == "gzip":
                    try:
                        with gzip.GzipFile(fileobj=io.BytesIO(body)) as compressed:
                            body = compressed.read(MAX_RESPONSE_BYTES + 1)
                    except (EOFError, OSError, zlib.error) as exc:
                        raise WeberCloudError("Weber cloud returned invalid gzip data.") from exc
                    if len(body) > MAX_RESPONSE_BYTES:
                        raise WeberCloudError("Weber cloud returned an oversized response.")
                return body
        except urllib.error.HTTPError as exc:
            message = f"Weber cloud returned HTTP {exc.code}"
            if exc.code in {401, 403}:
                if exc.code == 401:
                    self._token = None
                raise WeberCloudAuthError(message) from exc
            raise WeberCloudError(message) from exc
        except OSError as exc:
            raise WeberCloudError(f"Could not reach Weber cloud: {exc}") from exc

    def _request_payload(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        authenticated: bool = True,
    ) -> object:
        encoded = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(
            f"https://{API_HOST}{path}",
            data=encoded,
            method=method,
        )
        request.add_header("User-Agent", USER_AGENT)
        request.add_header("Accept-Encoding", "gzip")
        request.add_header("Accept", "application/json")
        if body is not None:
            request.add_header("Content-Type", "application/json; charset=UTF-8")
        if authenticated:
            request.add_header("Authorization", f"Bearer {self.token()}")
        raw = self._open(request)
        try:
            payload = json.loads(raw.decode())
        except (UnicodeDecodeError, ValueError) as exc:
            raise WeberCloudError("Weber cloud returned invalid JSON.") from exc
        if not isinstance(payload, (dict, list)):
            raise WeberCloudError("Weber cloud returned an unexpected response.")
        return payload

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        authenticated: bool = True,
    ) -> dict[str, Any]:
        payload = self._request_payload(
            method,
            path,
            body=body,
            authenticated=authenticated,
        )
        if not isinstance(payload, dict):
            raise WeberCloudError("Weber cloud returned an unexpected response.")
        return payload

    def authenticate(self) -> str:
        payload = self._request_json(
            "POST",
            "/2/devices/register",
            body={
                "password": self.config.device_password,
                "device_id": self.config.device_id,
                "client_secret": APP_CLIENT_SECRET,
                "client_id": APP_CLIENT_ID,
                "device_name": "Home Assistant",
                "device_type": "companion",
                "platform": "android",
                "platform_version": "33",
                "version": "2.10.1.2488",
            },
            authenticated=False,
        )
        token = payload.get("token", payload)
        if not isinstance(token, dict) or not isinstance(token.get("access_token"), str):
            raise WeberCloudAuthError("Weber cloud did not return an access token.")
        self._token = token["access_token"]
        expires_in = token.get("expires_in", 21_000)
        self._token_expiry = time.time() + int(
            expires_in if isinstance(expires_in, int) else 21_000
        )
        return self._token

    def token(self) -> str:
        token = self._token
        if token is None or time.time() >= self._token_expiry - 60:
            return self.authenticate()
        return token

    def token_needs_refresh(self) -> bool:
        """Return whether a new bearer token is required before more I/O."""

        return self._token is None or time.time() >= self._token_expiry - 60

    def associated_appliances(self) -> list[dict[str, Any]]:
        payload = self._request_json("GET", f"/2/devices/{self.config.device_id}/associated")
        devices = payload.get("devices", [])
        return (
            [row for row in devices if isinstance(row, dict)] if isinstance(devices, list) else []
        )

    def wake_messaging(self, appliance_id: str) -> None:
        """Prompt Weber's relay before opening the companion WebSocket."""

        normalized = appliance_id.replace(":", "").strip().lower()
        if not HEX_ID_RE.fullmatch(normalized):
            raise ValueError("Cloud appliance ID must be 32 hexadecimal characters.")
        request = urllib.request.Request(
            f"https://{MESSAGING_HOST}/1/messaging/device/{normalized}/status",
            method="GET",
        )
        request.add_header("User-Agent", USER_AGENT)
        request.add_header("Accept-Encoding", "gzip")
        request.add_header("Authorization", f"Bearer {self.token()}")
        self._open(request)
