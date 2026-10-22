"""Carry out the device's own effects and report its status."""

import asyncio
import logging
from collections.abc import Coroutine
from dataclasses import dataclass, field, replace
from typing import Any, Final

from paperroute.adapters.wifi import NullWifi
from paperroute.core.device import device_status
from paperroute.core.device_model import DeviceStatus
from paperroute.core.messages import (
    ApiKeyChanged,
    DeviceProbed,
    DeviceProbeFailed,
    Event,
    ProbeDevice,
)
from paperroute.core.state_model import State
from paperroute.ports import DeviceProbe, KeyStore, Observer, WifiControl

logger = logging.getLogger(__name__)

type DeviceEffect = ProbeDevice

PROBE_TIMEOUT: Final[float] = 10  # seconds


@dataclass(frozen=True)
class DevicePorts:
    """
    The adapters the device's own effects run on.

    Attributes
    ----------
    wifi : WifiControl
        Scans, joins and shares Wi-Fi.
    probe : DeviceProbe | None
        Gathers the device's facts; None where there is nothing to read.
    key : KeyStore | None
        Holds the API key; None when the app doesn't manage it.
    """

    wifi: WifiControl = field(default_factory=NullWifi)
    probe: DeviceProbe | None = None
    key: KeyStore | None = None


async def probe(*, ports: DevicePorts, queue: asyncio.Queue[Event]) -> None:
    """
    Gather the device's facts and post them.

    Parameters
    ----------
    ports : DevicePorts
        Device adapters.
    queue : asyncio.Queue[Event]
        Event queue.
    """
    if ports.probe is None:
        await queue.put(DeviceProbeFailed())
        return
    try:
        facts = await asyncio.wait_for(
            ports.probe.probe(),
            timeout=PROBE_TIMEOUT,
        )
    except TimeoutError:
        logger.error('device probe timed out')
        await queue.put(DeviceProbeFailed())
        return
    except Exception:  # pylint: disable=broad-exception-caught
        logger.exception('device probe failed')
        await queue.put(DeviceProbeFailed())
        return
    await queue.put(DeviceProbed(facts=facts))


def device_work(
    *,
    ports: DevicePorts,
    effect: DeviceEffect,
    queue: asyncio.Queue[Event],
) -> tuple[Coroutine[Any, Any, None], Event]:
    """
    Make the work for a device effect done in the background.

    Parameters
    ----------
    ports : DevicePorts
        Device adapters.
    effect : DeviceEffect
        Effect.
    queue : asyncio.Queue[Event]
        Event queue.

    Returns
    -------
    tuple[Coroutine[Any, Any, None], Event]
        The work, and the event that lets the app recover if it crashes.
    """
    match effect:
        case ProbeDevice():
            return probe(ports=ports, queue=queue), DeviceProbeFailed()


def key_known(*, ports: DevicePorts) -> ApiKeyChanged | None:
    """
    Say whether a key is set, for the app to know before it polls.

    Parameters
    ----------
    ports : DevicePorts
        Device adapters.

    Returns
    -------
    ApiKeyChanged | None
        Whether the key store holds a key; None without a key store.
    """
    if ports.key is None:
        return None
    return ApiKeyChanged(present=ports.key.present())


@dataclass
class Reported:
    """
    The device status reported last.

    Attributes
    ----------
    status : DeviceStatus | None
        The status, without its changing readings, or None before the
        first report.
    """

    status: DeviceStatus | None = None


def steady(*, status: DeviceStatus) -> DeviceStatus:
    """
    Drop the readings that change on every probe from a status.

    Parameters
    ----------
    status : DeviceStatus
        The status.

    Returns
    -------
    DeviceStatus
        The status with no uptime, CPU temperature, free memory or free
        disk.
    """
    if status.facts is None:
        return status
    facts = replace(
        status.facts,
        uptime_s=None,
        cpu_c=None,
        mem_free=None,
        disk_free=None,
    )
    return replace(status, facts=facts)


def report_status(
    *, observer: Observer, state: State, reported: Reported
) -> None:
    """
    Report the device status when it differs from the last one reported.

    Uptime, CPU temperature, free memory and free disk are reported but
    a change in them alone is not.

    Parameters
    ----------
    observer : Observer
        Receives the status.
    state : State
        State.
    reported : Reported
        The status reported last; updated when a new one is reported.
    """
    status = device_status(state=state)
    kept = steady(status=status)
    if kept != reported.status:
        observer.status_seen(status=status)
        reported.status = kept
