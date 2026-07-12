"""Typed runtime primitives shared by the panel controller and its tests."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine, Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any

LOGGER = logging.getLogger("weber_connect_runtime")


class ConnectionState(StrEnum):
    SETUP = "setup"
    SCANNING = "scanning"
    PAIRING = "pairing"
    CONNECTING = "connecting"
    ONLINE = "online"
    OFFLINE = "offline"
    HANDOFF = "handoff"


def parse_whole_number(value: object, label: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a whole number.")
    if not isinstance(value, (str, int, float)):
        raise ValueError(f"{label} must be a whole number.")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a whole number.") from exc
    if isinstance(value, float) and not value.is_integer():
        raise ValueError(f"{label} must be a whole number.")
    if isinstance(value, str) and value.strip() != str(parsed):
        raise ValueError(f"{label} must be a whole number.")
    return parsed


@dataclass(frozen=True, slots=True)
class BridgeSettings:
    address: str | None = None
    poll_seconds: int = 30
    handoff_minutes: int = 15

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> BridgeSettings:
        address = payload.get("address")
        if address is not None and not isinstance(address, str):
            raise ValueError("address must be a string or null.")
        poll_seconds = parse_whole_number(payload.get("poll_seconds", 30), "poll_seconds")
        handoff_minutes = parse_whole_number(
            payload.get("handoff_minutes", 15), "handoff_minutes"
        )
        return cls(
            address=address.strip() if address and address.strip() else None,
            poll_seconds=max(10, min(3600, poll_seconds)),
            handoff_minutes=max(0, min(240, handoff_minutes)),
        )

    def updated(self, payload: Mapping[str, Any]) -> BridgeSettings:
        allowed = {"poll_seconds", "handoff_minutes"}
        unknown = set(payload) - allowed
        if unknown:
            raise ValueError(f"Unknown setting: {sorted(unknown)[0]}.")
        poll_seconds = self.poll_seconds
        handoff_minutes = self.handoff_minutes
        if "poll_seconds" in payload:
            poll_seconds = max(
                10,
                min(3600, parse_whole_number(payload["poll_seconds"], "poll_seconds")),
            )
        if "handoff_minutes" in payload:
            handoff_minutes = max(
                0,
                min(240, parse_whole_number(payload["handoff_minutes"], "handoff_minutes")),
            )
        return replace(
            self,
            poll_seconds=poll_seconds,
            handoff_minutes=handoff_minutes,
        )

    def with_address(self, address: str | None) -> BridgeSettings:
        return replace(self, address=address)

    def as_dict(self) -> dict[str, Any]:
        return {
            "address": self.address,
            "poll_seconds": self.poll_seconds,
            "handoff_minutes": self.handoff_minutes,
        }


@dataclass(slots=True)
class RuntimeState:
    scanning: bool = False
    pairing: bool = False
    candidates: list[dict[str, Any]] = field(default_factory=list)
    setup_error: str | None = None
    handoff_active: bool = False
    handoff_until: float | None = None
    handoff_token: int = 0
    last_read_at: str | None = None
    last_read_ok: bool = False
    last_error: str | None = None
    last_good_state: dict[str, Any] = field(default_factory=dict)
    mqtt_published_at: str | None = None
    mqtt_error: str | None = None
    consecutive_failures: int = 0
    next_retry_seconds: int | None = None


class TaskSupervisor:
    """Own every background task and cancel them as one lifecycle unit."""

    def __init__(self) -> None:
        self._tasks: set[asyncio.Task[Any]] = set()
        self._closed = False

    def spawn(
        self,
        name: str,
        awaitable: Coroutine[Any, Any, Any],
        *,
        on_error: Callable[[BaseException], None] | None = None,
    ) -> asyncio.Task[Any]:
        if self._closed:
            raise RuntimeError("task supervisor is closed")
        task: asyncio.Task[Any] = asyncio.create_task(awaitable, name=name)
        self._tasks.add(task)

        def finished(completed: asyncio.Task[Any]) -> None:
            self._tasks.discard(completed)
            if completed.cancelled():
                return
            error = completed.exception()
            if error is None:
                return
            if on_error:
                on_error(error)
            else:
                LOGGER.error(
                    "Background task %s failed",
                    completed.get_name(),
                    exc_info=(type(error), error, error.__traceback__),
                )

        task.add_done_callback(finished)
        return task

    async def close(self) -> None:
        self._closed = True
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()

    @property
    def task_count(self) -> int:
        return len(self._tasks)


def retry_delay(base_seconds: int, failures: int, *, maximum: int = 300) -> int:
    """Bounded exponential retry delay; the first failure uses the base cadence."""
    if failures <= 1:
        return base_seconds
    exponent = min(failures - 1, 8)
    return int(min(maximum, base_seconds * (2**exponent)))
