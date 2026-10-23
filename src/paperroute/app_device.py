"""Carry out the device's own effects and report its status."""

import asyncio
import logging
from collections.abc import Coroutine
from dataclasses import dataclass, field, replace
from typing import Any, Final

from paperroute.adapters.resets import NullResets
from paperroute.adapters.wifi import NullWifi, hotspot_password
from paperroute.core.device import device_status
from paperroute.core.device_model import DeviceStatus, JoinOutcome
from paperroute.core.messages import (
    ApiKeyChanged,
    DeviceProbed,
    DeviceProbeFailed,
    Event,
    HotspotFailed,
    HotspotStarted,
    JoinWifi,
    PinEdit,
    ProbeDevice,
    ResetDevice,
    ResetDone,
    StartWifiSetup,
    StopWifiSetup,
    WifiJoined,
    WifiJoinFailed,
    WifiSetupStopped,
)
from paperroute.core.state_model import Places, State
from paperroute.ports import (
    DeviceProbe,
    KeyStore,
    Observer,
    Resets,
    TimetableBuilder,
    WifiControl,
)

logger = logging.getLogger(__name__)

type SetupEffect = StartWifiSetup | JoinWifi | StopWifiSetup
type DeviceEffect = ProbeDevice | ResetDevice | SetupEffect

PROBE_TIMEOUT: Final[float] = 10  # seconds


@dataclass
class HomeWifi:
    """
    The Wi-Fi connection in use when setup started.

    Attributes
    ----------
    uuid : str | None
        Its UUID, or None.
    """

    uuid: str | None = None


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
    resets : Resets
        Resets the settings or erases everything.
    radio : asyncio.Lock
        Held while Wi-Fi setup drives the radio, so its steps never
        overlap.
    home : HomeWifi
        The connection to go back to when setup ends without a join.
    """

    wifi: WifiControl = field(default_factory=NullWifi)
    probe: DeviceProbe | None = None
    key: KeyStore | None = None
    resets: Resets = field(default_factory=NullResets)
    radio: asyncio.Lock = field(default_factory=asyncio.Lock)
    home: HomeWifi = field(default_factory=HomeWifi)


async def probe(*, ports: DevicePorts, queue: asyncio.Queue[Event]) -> None:
    """
    Gather the device's facts and post them.

    The probe holds the radio lock, so it never reads Wi-Fi while setup
    is changing it.

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
        async with ports.radio:
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


async def reset(
    *,
    ports: DevicePorts,
    effect: ResetDevice,
    queue: asyncio.Queue[Event],
    builder: TimetableBuilder,
) -> None:
    """
    Stop any timetable rebuild, carry out a reset and post that it is done.

    Parameters
    ----------
    ports : DevicePorts
        Device adapters.
    effect : ResetDevice
        The reset asked for.
    queue : asyncio.Queue[Event]
        Event queue.
    builder : TimetableBuilder
        Rebuilds the timetable; stopped before any file is deleted.
        The reset holds the radio lock, so it waits for a join or a
        hotspot stop to finish.
    """
    await builder.stop()
    async with ports.radio:
        await ports.resets.reset(kind=effect.kind)
    await queue.put(ResetDone(kind=effect.kind))


async def setup_started(
    *, ports: DevicePorts, queue: asyncio.Queue[Event]
) -> None:
    """
    Scan, then start the setup hotspot with a new password.

    The scan runs first because the radio can't scan while it shares a
    hotspot. The password is posted for the panel and never logged.

    Parameters
    ----------
    ports : DevicePorts
        Device adapters.
    queue : asyncio.Queue[Event]
        Event queue.
    """
    async with ports.radio:
        ports.home.uuid = await ports.wifi.active_wifi()
        networks = await ports.wifi.scan()
        password = hotspot_password()
        started = await ports.wifi.start_hotspot(password=password)
    if not started:
        await queue.put(HotspotFailed())
        return
    await queue.put(HotspotStarted(networks=networks, password=password))


async def setup_join(
    *, ports: DevicePorts, effect: JoinWifi, queue: asyncio.Queue[Event]
) -> None:
    """
    Leave the hotspot and join a network; on failure start it again.

    Parameters
    ----------
    ports : DevicePorts
        Device adapters.
    effect : JoinWifi
        The network, and the hotspot password to start it again with.
    queue : asyncio.Queue[Event]
        Event queue.
    """
    ssid = effect.request.ssid
    async with ports.radio:
        await ports.wifi.stop_hotspot()
        outcome = await ports.wifi.join(request=effect.request)
        if outcome is JoinOutcome.JOINED:
            ports.home.uuid = None
            await queue.put(WifiJoined(ssid=ssid))
            return
        back = await ports.wifi.start_hotspot(password=effect.hotspot)
    logger.info('wifi join failed: %s', outcome.value)
    await queue.put(WifiJoinFailed(ssid=ssid, outcome=outcome, hotspot=back))


async def setup_stopped(
    *, ports: DevicePorts, queue: asyncio.Queue[Event]
) -> None:
    """
    Take the setup hotspot down, rejoin the network it left, say so.

    Parameters
    ----------
    ports : DevicePorts
        Device adapters.
    queue : asyncio.Queue[Event]
        Event queue.
    """
    async with ports.radio:
        await ports.wifi.stop_hotspot()
        home, ports.home.uuid = ports.home.uuid, None
        if home is not None:
            await ports.wifi.reconnect(uuid=home)
    await queue.put(WifiSetupStopped())


async def leftover_stopped(*, ports: DevicePorts) -> None:
    """
    Take down any setup hotspot, such as one a crashed run left up.

    Parameters
    ----------
    ports : DevicePorts
        Device adapters.
    """
    async with ports.radio:
        if not await ports.wifi.hotspot_saved():
            logger.debug('no setup hotspot left over')
            return
        await ports.wifi.stop_hotspot()


def setup_work(
    *, ports: DevicePorts, effect: SetupEffect, queue: asyncio.Queue[Event]
) -> tuple[Coroutine[Any, Any, None], Event]:
    """
    Make the work for a Wi-Fi setup effect.

    Parameters
    ----------
    ports : DevicePorts
        Device adapters.
    effect : SetupEffect
        Effect.
    queue : asyncio.Queue[Event]
        Event queue.

    Returns
    -------
    tuple[Coroutine[Any, Any, None], Event]
        The work, and the event that lets the app recover if it crashes.
    """
    match effect:
        case StartWifiSetup():
            return setup_started(ports=ports, queue=queue), HotspotFailed()
        case JoinWifi(request=request):
            # Hotspot state unknown; timeout or cancel still stops it.
            failed = WifiJoinFailed(
                ssid=request.ssid, outcome=JoinOutcome.FAILED, hotspot=False,
            )
            work = setup_join(ports=ports, effect=effect, queue=queue)
            return work, failed
        case StopWifiSetup():
            return setup_stopped(ports=ports, queue=queue), WifiSetupStopped()


def device_work(
    *,
    ports: DevicePorts,
    effect: DeviceEffect,
    queue: asyncio.Queue[Event],
    builder: TimetableBuilder,
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
    builder : TimetableBuilder
        Rebuilds the timetable; a reset stops it first.

    Returns
    -------
    tuple[Coroutine[Any, Any, None], Event]
        The work, and the event that lets the app recover if it crashes.
    """
    match effect:
        case ProbeDevice():
            return probe(ports=ports, queue=queue), DeviceProbeFailed()
        case ResetDevice(kind=kind):
            return (
                reset(
                    ports=ports, effect=effect, queue=queue, builder=builder,
                ),
                ResetDone(kind=kind),
            )
    return setup_work(ports=ports, effect=effect, queue=queue)


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


def report_pins(
    *, observer: Observer, before: Places, state: State, event: Event
) -> None:
    """
    Report the pins when the places changed or a pin was edited.

    An edit the reducer refused is reported too, so a page waiting on
    it is released.

    Parameters
    ----------
    observer : Observer
        Receives the pins.
    before : Places
        The places before the event.
    state : State
        State after the event.
    event : Event
        The event handled.
    """
    places = state.places
    if places != before or isinstance(event, PinEdit):
        pins_shown(observer=observer, places=places)


def pins_shown(*, observer: Observer, places: Places) -> None:
    """
    Report the pins and which one is on screen.

    Parameters
    ----------
    observer : Observer
        Receives the pins.
    places : Places
        The pins and which place is shown.
    """
    on_screen = places.unpinned is None and bool(places.pins)
    observer.pins_seen(
        pins=places.pins,
        shown=places.current if on_screen else None,
    )
