"""Typed user options for Weber Connect Unofficial."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping

from .const import (
    CONF_CONNECTION,
    CONF_CONNECTION_MODE,
    CONF_PROBE_NAME_PREFIX,
    CONF_PROBES,
)


class ConnectionMode(StrEnum):
    """How Home Assistant should receive live Weber data."""

    PHONE_AND_HOME_ASSISTANT = "phone_and_home_assistant"
    HOME_ASSISTANT_ONLY = "home_assistant_only"


@dataclass(frozen=True, slots=True)
class WeberOptions:
    """Validated effective options with product defaults."""

    connection_mode: ConnectionMode = ConnectionMode.PHONE_AND_HOME_ASSISTANT
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
        try:
            mode = ConnectionMode(
                connection.get(
                    CONF_CONNECTION_MODE,
                    ConnectionMode.PHONE_AND_HOME_ASSISTANT,
                )
            )
        except ValueError:
            mode = ConnectionMode.PHONE_AND_HOME_ASSISTANT

        names = (
            _probe_name(probes, 1),
            _probe_name(probes, 2),
            _probe_name(probes, 3),
            _probe_name(probes, 4),
        )
        return cls(
            connection_mode=mode,
            probe_names=names,
        )

    def as_dict(self) -> dict[str, Any]:
        """Serialize options into native Home Assistant form sections."""

        return {
            CONF_CONNECTION: {
                CONF_CONNECTION_MODE: self.connection_mode.value,
            },
            CONF_PROBES: {
                f"{CONF_PROBE_NAME_PREFIX}{number}": self.probe_name(number)
                for number in range(1, 5)
            },
        }


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _probe_name(probes: Mapping[str, Any], number: int) -> str:
    return str(probes.get(f"{CONF_PROBE_NAME_PREFIX}{number}", "")).strip()[:40]
