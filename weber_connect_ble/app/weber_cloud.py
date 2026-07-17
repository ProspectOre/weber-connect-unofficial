"""Experimental Weber cloud transport used as a BLE fallback.

The cloud API is private and undocumented.  This module deliberately keeps the
surface small: companion authentication, appliance association, active-session
discovery, and REST cook-history snapshots.  It never configures Wi-Fi or sends
grill-control commands.
"""

from __future__ import annotations

import gzip
import json
import re
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

API_HOST = "api.walker-cloud.com"
USER_AGENT = "okhttp/5.3.0"
STALE_GRACE_SECONDS = 60.0

# Application-global credentials embedded in the Weber Android application.
# They identify the application, not an individual user.  Personal companion
# credentials are generated or supplied at runtime and stored in /data only.
APP_CLIENT_ID = "qyw4CGeb/i93BrA0KAUuGtPyKImr+nUKc8lHxFdt"
APP_CLIENT_SECRET = "ekEHLyHw+Ru3H25mH4a9f2OKCMILnMx+YSN2dFIB2zB0PP8NGAnSPTw"

HEX_ID_RE = re.compile(r"^[0-9a-f]{32}$")
VERIFICATION_CODE_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class WeberCloudError(RuntimeError):
    """Cloud transport or response failure."""


class WeberCloudAuthError(WeberCloudError):
    """Companion credentials were rejected."""


@dataclass(frozen=True, slots=True)
class CloudConfig:
    """Private persisted cloud configuration."""

    device_id: str
    device_password: str
    enabled: bool = True
    temperature_unit: str = "fahrenheit"
    identity_source: str = "manual"
    appliance_id: str | None = None

    @classmethod
    def from_mapping(cls, payload: dict[str, Any]) -> CloudConfig:
        device_id = str(payload.get("device_id") or "").replace(":", "").strip().lower()
        password = str(payload.get("device_password") or "").strip()
        if not HEX_ID_RE.fullmatch(device_id):
            raise ValueError("Cloud device ID must be 32 hexadecimal characters.")
        if not password or len(password) > 256:
            raise ValueError("Cloud device password is required and must be at most 256 characters.")
        unit = str(payload.get("temperature_unit") or "fahrenheit").strip().lower()
        if unit not in {"fahrenheit", "celsius", "deci_celsius"}:
            raise ValueError("Cloud temperature unit must be fahrenheit, celsius, or deci_celsius.")
        source = str(payload.get("identity_source") or "manual").strip().lower()
        if source not in {"manual", "bridge"}:
            raise ValueError("Cloud identity source must be manual or bridge.")
        appliance_id_value = payload.get("appliance_id")
        appliance_id = (
            str(appliance_id_value).replace(":", "").strip().lower()
            if appliance_id_value
            else None
        )
        if appliance_id is not None and not HEX_ID_RE.fullmatch(appliance_id):
            raise ValueError("Cloud appliance ID must be 32 hexadecimal characters.")
        return cls(
            device_id=device_id,
            device_password=password,
            enabled=payload.get("enabled") is not False,
            temperature_unit=unit,
            identity_source=source,
            appliance_id=appliance_id,
        )

    @classmethod
    def generate(cls, companion_id: str) -> CloudConfig:
        device_id = companion_id.replace(":", "").replace("-", "").strip().lower()
        if not HEX_ID_RE.fullmatch(device_id):
            device_id = secrets.token_hex(16)
        return cls(
            device_id=device_id,
            device_password=secrets.token_hex(16),
            temperature_unit="deci_celsius",
            identity_source="bridge",
        )

    def with_temperature_unit(self, temperature_unit: str) -> CloudConfig:
        if temperature_unit not in {"fahrenheit", "celsius", "deci_celsius"}:
            raise ValueError("Cloud temperature unit must be fahrenheit, celsius, or deci_celsius.")
        return CloudConfig(
            device_id=self.device_id,
            device_password=self.device_password,
            enabled=self.enabled,
            temperature_unit=temperature_unit,
            identity_source=self.identity_source,
            appliance_id=self.appliance_id,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "device_id": self.device_id,
            "device_password": self.device_password,
            "enabled": self.enabled,
            "temperature_unit": self.temperature_unit,
            "identity_source": self.identity_source,
            "appliance_id": self.appliance_id,
        }

    def public_dict(self) -> dict[str, Any]:
        return {
            "configured": True,
            "enabled": self.enabled,
            "device_id_suffix": self.device_id[-6:],
            "temperature_unit": self.temperature_unit,
            "identity_source": self.identity_source,
            "appliance_id_available": self.appliance_id is not None,
        }

    def with_enabled(self, enabled: bool) -> CloudConfig:
        return CloudConfig(
            device_id=self.device_id,
            device_password=self.device_password,
            enabled=enabled,
            temperature_unit=self.temperature_unit,
            identity_source=self.identity_source,
            appliance_id=self.appliance_id,
        )

    def with_appliance_id(self, appliance_id: str) -> CloudConfig:
        normalized = appliance_id.replace(":", "").strip().lower()
        if not HEX_ID_RE.fullmatch(normalized):
            raise ValueError("Cloud appliance ID must be 32 hexadecimal characters.")
        return CloudConfig(
            device_id=self.device_id,
            device_password=self.device_password,
            enabled=self.enabled,
            temperature_unit=self.temperature_unit,
            identity_source=self.identity_source,
            appliance_id=normalized,
        )


@dataclass(frozen=True, slots=True)
class CloudPollResult:
    status: dict[str, Any]
    session_id: str
    after_id: int
    snapshot_count: int


def normalize_cloud_temperature(raw: object, unit: str) -> tuple[float, float] | None:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or raw == 0:
        return None
    value = float(raw)
    if unit == "deci_celsius":
        celsius = value / 10.0
        fahrenheit = celsius * 9.0 / 5.0 + 32.0
    elif unit == "celsius":
        celsius = value
        fahrenheit = celsius * 9.0 / 5.0 + 32.0
    else:
        fahrenheit = value
        celsius = (fahrenheit - 32.0) * 5.0 / 9.0
    return round(fahrenheit, 1), round(celsius, 1)


def cloud_status_from_snapshot(snapshot: dict[str, Any], unit: str) -> dict[str, Any]:
    probes: list[dict[str, Any]] = []
    data = snapshot.get("data")
    rows = data.get("probe_status", []) if isinstance(data, dict) else []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        index = row.get("index")
        if isinstance(index, bool) or not isinstance(index, int) or index < 0:
            continue
        converted = normalize_cloud_temperature(row.get("temperature"), unit)
        if converted is None:
            continue
        fahrenheit, celsius = converted
        probes.append(
            {
                "probe_number": index + 1,
                "label": f"Probe {index + 1}",
                "state": "CONNECTED",
                "probe_type": None,
                "battery_level": None,
                "probe_temp_f": fahrenheit,
                "probe_temp_c": celsius,
            }
        )
    return {
        "kind": "cloud_cook_history",
        "probe_count": len(probes),
        "probes": probes,
        "snapshot_id": snapshot.get("snapshot_id"),
        "server_timestamp": snapshot.get("server_timestamp"),
    }


def resolve_associated_appliance_id(
    appliances: list[dict[str, Any]],
    expected_appliance_id: str | None = None,
) -> str | None:
    """Resolve the cloud oven id without leaking unrelated appliance access."""

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
    """Synchronous private-API client; callers run it in an executor thread."""

    def __init__(self, config: CloudConfig, *, timeout: float = 20.0) -> None:
        self.config = config
        self.timeout = timeout
        self._token: str | None = None
        self._token_expiry = 0.0
        self._session_id: str | None = None
        self._after_id = 0
        self._last_status: dict[str, Any] | None = None
        self._last_snapshot_at = 0.0

    def _open(self, request: urllib.request.Request) -> bytes:
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = bytes(response.read())
                if response.headers.get("Content-Encoding") == "gzip":
                    body = gzip.decompress(body)
                return body
        except urllib.error.HTTPError as exc:
            detail = exc.read(512).decode("utf-8", "replace").strip()
            message = f"Weber cloud returned HTTP {exc.code}"
            if detail:
                message += f": {detail}"
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
        encoded = json.dumps(body).encode("utf-8") if body is not None else None
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
            payload = json.loads(raw.decode("utf-8"))
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
                "device_name": "Home Assistant Weber Bridge",
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
        self._token_expiry = time.time() + int(expires_in if isinstance(expires_in, int) else 21_000)
        return self._token

    def token(self) -> str:
        if self._token is None or time.time() >= self._token_expiry - 60:
            return self.authenticate()
        return self._token

    def associated_appliances(self) -> list[dict[str, Any]]:
        payload = self._request_json(
            "GET", f"/2/devices/{self.config.device_id}/associated"
        )
        devices = payload.get("devices", [])
        return [row for row in devices if isinstance(row, dict)] if isinstance(devices, list) else []

    def associate(self, verification_code: str) -> dict[str, Any]:
        code = verification_code.strip()
        if not VERIFICATION_CODE_RE.fullmatch(code):
            raise ValueError("Verification code contains unsupported characters.")
        quoted = urllib.parse.quote(code, safe="")
        return self._request_json(
            "POST",
            f"/2/devices/pairing/{quoted}/companion",
            body={},
        )

    def latest_session_id(self, appliance_id: str) -> str | None:
        appliance = urllib.parse.quote(appliance_id, safe="")
        payload = self._request_payload(
            "GET", f"/cook-history/1/appliance/{appliance}/sessions"
        )
        sessions: object
        if isinstance(payload, list):
            sessions = payload
        elif isinstance(payload, dict):
            sessions = payload.get("sessions", payload.get("items", []))
        else:
            sessions = []
        if not isinstance(sessions, list) or not sessions:
            return None
        rows = [row for row in sessions if isinstance(row, dict)]
        if not rows:
            return None

        def sort_key(row: dict[str, Any]) -> tuple[int, float, str]:
            value = (
                row.get("server_timestamp")
                or row.get("updated_at")
                or row.get("created_at")
                or ""
            )
            try:
                return (1, float(value), "")
            except (TypeError, ValueError):
                return (0, 0.0, str(value))

        latest = max(rows, key=sort_key)
        value = latest.get("session_id") or latest.get("id")
        return str(value) if value else None

    def snapshots(self, appliance_id: str, session_id: str, after_id: int) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        cursor = max(0, after_id)
        appliance = urllib.parse.quote(appliance_id, safe="")
        session = urllib.parse.quote(session_id, safe="")
        while True:
            payload = self._request_json(
                "GET",
                f"/cook-history/1/appliance/{appliance}/session/{session}"
                f"/snapshots?limit=1000&after_id={cursor}",
            )
            page = payload.get("snapshots", [])
            if not isinstance(page, list):
                raise WeberCloudError("Weber cloud returned an invalid snapshot page.")
            valid = [row for row in page if isinstance(row, dict)]
            if not valid:
                break
            rows.extend(valid)
            ids = [row.get("snapshot_id") for row in valid]
            numeric_ids = [value for value in ids if isinstance(value, int) and not isinstance(value, bool)]
            if numeric_ids:
                next_cursor = max(cursor, max(numeric_ids))
                if next_cursor == cursor and len(valid) == 1000:
                    break
                cursor = next_cursor
            if len(valid) < 1000 or not numeric_ids:
                break
        return rows

    def poll(self, appliance_id: str) -> CloudPollResult | None:
        session_id = self.latest_session_id(appliance_id)
        if not session_id:
            self._session_id = None
            self._after_id = 0
            self._last_status = None
            self._last_snapshot_at = 0.0
            return None
        if session_id != self._session_id:
            self._session_id = session_id
            self._after_id = 0
            self._last_status = None
            self._last_snapshot_at = 0.0
        snapshots = self.snapshots(appliance_id, session_id, self._after_id)
        for snapshot in snapshots:
            snapshot_id = snapshot.get("snapshot_id")
            if isinstance(snapshot_id, int) and not isinstance(snapshot_id, bool):
                self._after_id = max(self._after_id, snapshot_id)
        if not snapshots:
            if (
                self._last_status is not None
                and time.monotonic() - self._last_snapshot_at <= STALE_GRACE_SECONDS
            ):
                return CloudPollResult(
                    status=self._last_status,
                    session_id=session_id,
                    after_id=self._after_id,
                    snapshot_count=0,
                )
            return None
        status = cloud_status_from_snapshot(snapshots[-1], self.config.temperature_unit)
        self._last_status = status
        self._last_snapshot_at = time.monotonic()
        return CloudPollResult(
            status=status,
            session_id=session_id,
            after_id=self._after_id,
            snapshot_count=len(snapshots),
        )
