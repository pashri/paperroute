"""Carry out the device's own effects and report its status."""

# An app package keeps a twin of this module until it switches over.
# pylint: disable=duplicate-code

import asyncio
import logging
from collections.abc import Coroutine
from dataclasses import dataclass, field, replace
from typing import Any, Final, Protocol

from paperroute_device.adapters.resets import NullResets
from paperroute_device.adapters.wifi import NullWifi, hotspot_password
from paperroute_device.core.app import App
from paperroute_device.core.device import device_status
from paperroute_device.core.device_events import ApiKeyChanged
from paperroute_device.core.device_model import (
    DeviceFacts,
    DeviceKey,
    DeviceStatus,
    JoinOutcome,
    missing_keys,
)
from paperroute_device.core.kit_effects import (
    JoinWifi,
    ProbeDevice,
    ResetDevice,
    StartWifiSetup,
    StopWifiSetup,
)
from paperroute_device.core.kit_events import (
    DeviceProbed,
    DeviceProbeFailed,
    HotspotFailed,
    HotspotStarted,
    ResetDone,
    WifiJoined,
    WifiJoinFailed,
    WifiSetupStopped,
)
from paperroute_device.core.kit_state import KitState
from paperroute_device.ports import (
    DeviceObserver,
    DeviceProbe,
    Resets,
    WifiControl,
)

logger = logging.getLogger(__name__)

type SetupEffect = StartWifiSetup | JoinWifi | StopWifiSetup
type DeviceEffect = ProbeDevice | ResetDevice | SetupEffect
type Work = tuple[Coroutine[Any, Any, None], object]

PROBE_TIMEOUT: Final[float] = 10  # seconds


class AppProbe(Protocol):
    """Finds out what the app wants reported with the device's facts."""

    async def probe(self) -> object:
        """
        Find out the app's part of the facts.

        Returns
        -------
        object
            The facts' `app` part.
        """


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
    keys : tuple[DeviceKey, ...]
        The API keys the device keeps; none when the app manages none.
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
    keys: tuple[DeviceKey, ...] = ()
    resets: Resets = field(default_factory=NullResets)
    radio: asyncio.Lock = field(default_factory=asyncio.Lock)
    home: HomeWifi = field(default_factory=HomeWifi)


async def probe(
    *, ports: DevicePorts, app: AppProbe, queue: asyncio.Queue[object],
) -> None:
    """
    Gather the device's facts, with the app's part, and post them.

    The probe holds the radio lock, so it never reads Wi-Fi while setup
    is changing it.

    Parameters
    ----------
    ports : DevicePorts
        Device adapters.
    app : AppProbe
        Gives the facts' `app` part.
    queue : asyncio.Queue[object]
        Event queue.
    """
    facts = None if ports.probe is None else await probed(
        ports=ports, app=app,
    )
    if facts is None:
        await queue.put(DeviceProbeFailed())
        return
    await queue.put(DeviceProbed(facts=facts))


async def gathered(
    *, system: DeviceProbe, app: AppProbe,
) -> DeviceFacts:
    """
    Gather the system's facts, then the app's part.

    Parameters
    ----------
    system : DeviceProbe
        Reads the system's facts.
    app : AppProbe
        Gives the facts' `app` part.

    Returns
    -------
    DeviceFacts
        The system's facts with the app's part.
    """
    facts = await system.probe()
    return replace(facts, app=await app.probe())


async def probed(
    *, ports: DevicePorts, app: AppProbe,
) -> DeviceFacts | None:
    """
    Gather the device's facts under the radio lock.

    Parameters
    ----------
    ports : DevicePorts
        Device adapters; their probe is set.
    app : AppProbe
        Gives the facts' `app` part.

    Returns
    -------
    DeviceFacts | None
        The facts; None when the probe timed out or failed.
    """
    assert ports.probe is not None
    try:
        async with ports.radio:
            return await asyncio.wait_for(
                gathered(system=ports.probe, app=app),
                timeout=PROBE_TIMEOUT,
            )
    except TimeoutError:
        logger.error('device probe timed out')
    except Exception:  # pylint: disable=broad-exception-caught
        logger.exception('device probe failed')
    return None


async def reset(
    *,
    ports: DevicePorts,
    effect: ResetDevice,
    queue: asyncio.Queue[object],
) -> None:
    """
    Carry out a reset and post that it is done.

    The reset holds the radio lock, so it waits for a join or a hotspot
    stop to finish.

    Parameters
    ----------
    ports : DevicePorts
        Device adapters.
    effect : ResetDevice
        The reset asked for.
    queue : asyncio.Queue[object]
        Event queue.
    """
    async with ports.radio:
        await ports.resets.reset(kind=effect.kind)
    await queue.put(ResetDone(kind=effect.kind))


async def setup_started(
    *, ports: DevicePorts, queue: asyncio.Queue[object],
) -> None:
    """
    Scan, then start the setup hotspot with a new password.

    The scan runs first because the radio can't scan while it shares a
    hotspot. The password is posted for the panel and never logged.

    Parameters
    ----------
    ports : DevicePorts
        Device adapters.
    queue : asyncio.Queue[object]
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
    *, ports: DevicePorts, effect: JoinWifi, queue: asyncio.Queue[object],
) -> None:
    """
    Leave the hotspot and join a network; on failure start it again.

    Parameters
    ----------
    ports : DevicePorts
        Device adapters.
    effect : JoinWifi
        The network, and the hotspot password to start it again with.
    queue : asyncio.Queue[object]
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
    *, ports: DevicePorts, queue: asyncio.Queue[object],
) -> None:
    """
    Take the setup hotspot down, rejoin the network it left, say so.

    Parameters
    ----------
    ports : DevicePorts
        Device adapters.
    queue : asyncio.Queue[object]
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
    *,
    ports: DevicePorts,
    effect: SetupEffect,
    queue: asyncio.Queue[object],
) -> Work:
    """
    Make the work for a Wi-Fi setup effect.

    Parameters
    ----------
    ports : DevicePorts
        Device adapters.
    effect : SetupEffect
        Effect.
    queue : asyncio.Queue[object]
        Event queue.

    Returns
    -------
    Work
        The work, and the event that lets the kit recover if it crashes.
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
    app: AppProbe,
    effect: DeviceEffect,
    queue: asyncio.Queue[object],
) -> Work:
    """
    Make the work for a device effect done in the background.

    Parameters
    ----------
    ports : DevicePorts
        Device adapters.
    app : AppProbe
        Gives the facts' `app` part when probing.
    effect : DeviceEffect
        Effect.
    queue : asyncio.Queue[object]
        Event queue.

    Returns
    -------
    Work
        The work, and the event that lets the kit recover if it crashes.
    """
    match effect:
        case ProbeDevice():
            work = probe(ports=ports, app=app, queue=queue)
            return work, DeviceProbeFailed()
        case ResetDevice(kind=kind):
            work = reset(ports=ports, effect=effect, queue=queue)
            return work, ResetDone(kind=kind)
    return setup_work(ports=ports, effect=effect, queue=queue)


def key_known(*, ports: DevicePorts) -> ApiKeyChanged | None:
    """
    Say which keys are missing, for the app to know before it starts.

    Parameters
    ----------
    ports : DevicePorts
        Device adapters.

    Returns
    -------
    ApiKeyChanged | None
        The keys whose stores hold none; None with no keys.
    """
    if not ports.keys:
        return None
    return ApiKeyChanged(missing=missing_keys(keys=ports.keys))


@dataclass
class Reported:
    """
    The statuses reported last.

    Attributes
    ----------
    status : DeviceStatus | None
        The device status, without its changing readings, or None
        before the first report.
    app : dict[str, object] | None
        The app's status, or None before the first report.
    """

    status: DeviceStatus | None = None
    app: dict[str, object] | None = None


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


def report_status[S](
    *, observer: DeviceObserver, kit: KitState[S], reported: Reported,
) -> None:
    """
    Report the device status when it differs from the last one.

    Uptime, CPU temperature, free memory and free disk are reported but
    a change in them alone is not.

    Parameters
    ----------
    observer : DeviceObserver
        Receives the status.
    kit : KitState[S]
        Kit state.
    reported : Reported
        The statuses reported last; updated when a new one is reported.
    """
    status = device_status(device=kit.device)
    kept = steady(status=status)
    if kept != reported.status:
        observer.status_seen(status=status)
        reported.status = kept


def report_app_status[S](
    *,
    observer: DeviceObserver,
    app: App[S],
    kit: KitState[S],
    reported: Reported,
) -> None:
    """
    Report the app's status when it differs from the last one.

    Parameters
    ----------
    observer : DeviceObserver
        Receives the status.
    app : App[S]
        The app, which gives its status.
    kit : KitState[S]
        Kit state.
    reported : Reported
        The statuses reported last; updated when a new one is reported.
    """
    status = dict(app.status(state=kit.app))
    if status != reported.app:
        observer.app_seen(status=status)
        reported.app = status
