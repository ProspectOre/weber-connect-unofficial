"""Typed user options for Weber Connect Unofficial."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping

from .const import (
    CONF_ADVANCED,
    CONF_CONNECTION,
    CONF_CONNECTION_MODE,
    CONF_LOCAL_FALLBACK,
    CONF_POLL_SECONDS,
    CONF_PROBE_NAME_PREFIX,
    CONF_PROBES,
    CONF_REMOTE_CONTROLS,
    DEFAULT_LOCAL_FALLBACK,
    DEFAULT_POLL_SECONDS,
    DEFAULT_REMOTE_CONTROLS,
)


class ConnectionMode(StrEnum):
    """How Home Assistant should receive live Weber data."""

    PHONE_AND_HOME_ASSISTANT = "phone_and_home_assistant"
    HOME_ASSISTANT_ONLY = "home_assistant_only"


@dataclass(frozen=True, slots=True)
class WeberOptions:
    """Validated effective options with product defaults."""

    connection_mode: ConnectionMode = ConnectionMode.PHONE_AND_HOME_ASSISTANT
    remote_controls: bool = DEFAULT_REMOTE_CONTROLS
    poll_seconds: int = DEFAULT_POLL_SECONDS
    local_fallback: bool = DEFAULT_LOCAL_FALLBACK
    probe_names: tuple[str, str, str, str] = ("", "", "", "")

    @property
    def cloud_enabled(self) -> bool:
        """Whether online reads are enabled for phone + Home Assistant use."""

        return self.connection_mode is ConnectionMode.PHONE_AND_HOME_ASSISTANT

    def probe_name(self, number: int) -> str:
        """Return a cleaned optional nickname for one physical probe slot."""

        if not 1 <= number <= 4:
            raise ValueError("Probe number must be between 1 and 4.")
        return self.probe_names[number - 1]

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> WeberOptions:
        """Build effective options from Home Assistant's stored mapping."""

        connection = _mapping(raw.get(CONF_CONNECTION))
        probes = _mapping(raw.get(CONF_PROBES))
        advanced = _mapping(raw.get(CONF_ADVANCED))

        try:
            mode = ConnectionMode(
                connection.get(
                    CONF_CONNECTION_MODE,
                    ConnectionMode.PHONE_AND_HOME_ASSISTANT,
                )
            )
        except ValueError:
            mode = ConnectionMode.PHONE_AND_HOME_ASSISTANT

        try:
            poll_seconds = int(advanced.get(CONF_POLL_SECONDS, DEFAULT_POLL_SECONDS))
        except TypeError, ValueError:
            poll_seconds = DEFAULT_POLL_SECONDS
        if poll_seconds not in (10, 30, 60, 120):
            poll_seconds = DEFAULT_POLL_SECONDS

        names = (
            _probe_name(probes, 1),
            _probe_name(probes, 2),
            _probe_name(probes, 3),
            _probe_name(probes, 4),
        )
        return cls(
            connection_mode=mode,
            remote_controls=bool(connection.get(CONF_REMOTE_CONTROLS, DEFAULT_REMOTE_CONTROLS)),
            poll_seconds=poll_seconds,
            local_fallback=bool(advanced.get(CONF_LOCAL_FALLBACK, DEFAULT_LOCAL_FALLBACK)),
            probe_names=names,
        )

    def as_dict(self) -> dict[str, Any]:
        """Serialize options into native Home Assistant form sections."""

        return {
            CONF_CONNECTION: {
                CONF_CONNECTION_MODE: self.connection_mode.value,
                CONF_REMOTE_CONTROLS: self.remote_controls,
            },
            CONF_PROBES: {
                f"{CONF_PROBE_NAME_PREFIX}{number}": self.probe_name(number)
                for number in range(1, 5)
            },
            CONF_ADVANCED: {
                CONF_POLL_SECONDS: self.poll_seconds,
                CONF_LOCAL_FALLBACK: self.local_fallback,
            },
        }


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _probe_name(probes: Mapping[str, Any], number: int) -> str:
    return str(probes.get(f"{CONF_PROBE_NAME_PREFIX}{number}", "")).strip()[:40]
