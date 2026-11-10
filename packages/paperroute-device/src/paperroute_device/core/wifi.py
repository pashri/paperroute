"""Wi-Fi setup from the panel."""

# An app package keeps a twin of this module until it switches over.
# pylint: disable=duplicate-code

import math
from dataclasses import replace
from datetime import timedelta, tzinfo
from typing import Final

from paperroute_device.core.device_events import JoinAsked
from paperroute_device.core.device_model import (
    JoinOutcome,
    WifiPhase,
    WifiSetup,
)
from paperroute_device.core.device_state import DeviceState, DeviceStep
from paperroute_device.core.kit_effects import (
    JoinWifi,
    ProbeDevice,
    StartWifiSetup,
    StopWifiSetup,
)
from paperroute_device.core.kit_events import (
    HotspotFailed,
    HotspotStarted,
    WifiEvent,
    WifiJoined,
    WifiJoinFailed,
)
from paperroute_device.core.panel_views import WifiSetupView
from paperroute_device.core.text import clock_text
from paperroute_device.core.wifi_text import (
    SETUP_ADDRESS,
    SETUP_SSID,
    START_FAILED,
)
from paperroute_hardware.core.knob_events import Knob, KnobPressed

SETUP_FOR: Final[timedelta] = timedelta(minutes=10)
WIFI_SPECIAL: Final[str] = '\\;,:"'
JOIN_ERRORS: Final[dict[JoinOutcome, str]] = {
    JoinOutcome.WRONG_PASSWORD: "Couldn't join {ssid}: wrong password",
    JoinOutcome.NOT_FOUND: "Couldn't join {ssid}: not found",
    JoinOutcome.FAILED: "Couldn't join {ssid}",
}
OPEN_PHASES: Final[frozenset[WifiPhase]] = frozenset(
    {WifiPhase.HOTSPOT, WifiPhase.FAILED},
)
TOP_PRESS: Final[KnobPressed] = KnobPressed(knob=Knob.TOP)


def wifi_escaped(*, text: str) -> str:
    """
    Escape a field of a WIFI: code.

    Parameters
    ----------
    text : str
        A network name or password.

    Returns
    -------
    str
        The text with a backslash before each of ``\\ ; , : "``.
    """
    return ''.join(
        f'\\{char}' if char in WIFI_SPECIAL else char for char in text
    )


def wifi_qr(*, password: str) -> str:
    """
    Give the text of the code that joins the setup hotspot.

    Parameters
    ----------
    password : str
        The hotspot password.

    Returns
    -------
    str
        A WIFI: code for a WPA network, each field escaped.
    """
    ssid = wifi_escaped(text=SETUP_SSID)
    return f'WIFI:T:WPA;S:{ssid};P:{wifi_escaped(text=password)};;'


def shown_password(*, password: str) -> str:
    """
    Split a password into groups of four to read it out.

    Parameters
    ----------
    password : str
        The password.

    Returns
    -------
    str
        Groups of four characters joined by spaces.
    """
    return ' '.join(
        password[start:start + 4]
        for start in range(0, len(password), 4)
    )


def wifi_setup_view(*, device: DeviceState, zone: tzinfo) -> WifiSetupView:
    """
    Build the Wi-Fi setup screen.

    Parameters
    ----------
    device : DeviceState
        Device state, with a setup open.
    zone : tzinfo
        Time zone of the header's clock.

    Returns
    -------
    WifiSetupView
        The phase, the code, the password in groups of four and the
        whole minutes left, rounded up.
    """
    setup = device.panel.wifi or WifiSetup(phase=WifiPhase.SCANNING)
    seconds = 0.0
    if setup.until is not None:
        seconds = max((setup.until - device.now).total_seconds(), 0.0)
    return WifiSetupView(
        clock=clock_text(moment=device.now, zone=zone),
        phase=setup.phase,
        qr=wifi_qr(password=setup.password),
        password=shown_password(password=setup.password),
        address=SETUP_ADDRESS,
        minutes_left=math.ceil(seconds / 60),
        ssid=setup.ssid,
        error=setup.error,
    )


def with_setup(*, device: DeviceState, setup: WifiSetup | None) -> DeviceState:
    """
    Set the open Wi-Fi setup.

    Parameters
    ----------
    device : DeviceState
        Device state.
    setup : WifiSetup | None
        The setup, or None to close it.

    Returns
    -------
    DeviceState
        Device state with the setup set.
    """
    return replace(device, panel=replace(device.panel, wifi=setup))


def setup_started(*, device: DeviceState) -> DeviceStep:
    """
    Open Wi-Fi setup and scan, keeping the menu under it.

    Parameters
    ----------
    device : DeviceState
        Device state.

    Returns
    -------
    DeviceStep
        Device state scanning and a StartWifiSetup; unchanged while a setup is
        open or a power action or reset is under way. The caller
        redraws.
    """
    busy = device.power is not None or device.panel.resetting is not None
    if busy or device.panel.wifi is not None:
        return device, ()
    setup = WifiSetup(phase=WifiPhase.SCANNING)
    return with_setup(device=device, setup=setup), (StartWifiSetup(),)


def setup_closed(*, device: DeviceState) -> DeviceStep:
    """
    Close an open Wi-Fi setup and take its hotspot down.

    Parameters
    ----------
    device : DeviceState
        Device state.

    Returns
    -------
    DeviceStep
        Device state with no setup and a StopWifiSetup; unchanged with none
        open.
    """
    if device.panel.wifi is None:
        return device, ()
    return with_setup(device=device, setup=None), (StopWifiSetup(),)


def setup_knob(*, device: DeviceState, event: object) -> DeviceStep:
    """
    Apply a touch to the open Wi-Fi setup.

    Parameters
    ----------
    device : DeviceState
        Device state, with a setup open.
    event : object
        The touch.

    Returns
    -------
    DeviceStep
        A top press with the hotspot up or failed closes it; anything
        else, and every touch while scanning or joining, does nothing.
    """
    setup = device.panel.wifi
    if setup is None or setup.phase not in OPEN_PHASES:
        return device, ()
    if event != TOP_PRESS:
        return device, ()
    return setup_closed(device=device)


def setup_expired(*, device: DeviceState) -> DeviceStep:
    """
    Close a hotspot that has been up for `SETUP_FOR`.

    Parameters
    ----------
    device : DeviceState
        Device state at the tick's time.

    Returns
    -------
    DeviceStep
        Device state with the setup closed and a StopWifiSetup once the
        hotspot or failed setup reaches its time; unchanged otherwise.
    """
    setup = device.panel.wifi
    if setup is None or setup.phase not in OPEN_PHASES:
        return device, ()
    if setup.until is None or device.now < setup.until:
        return device, ()
    return setup_closed(device=device)


def join_failed(*, device: DeviceState, event: WifiJoinFailed) -> DeviceStep:
    """
    Show the setup again after a failed join.

    Parameters
    ----------
    device : DeviceState
        Device state.
    event : WifiJoinFailed
        How the join failed.

    Returns
    -------
    DeviceStep
        Device state back on the hotspot, or failed when the hotspot did not
        come back, with the reason and a fresh `SETUP_FOR`; with no
        setup open any more, a StopWifiSetup for a hotspot back up.
    """
    setup = device.panel.wifi
    if setup is None:
        return device, (StopWifiSetup(),) if event.hotspot else ()
    setup = replace(
        setup,
        phase=WifiPhase.HOTSPOT if event.hotspot else WifiPhase.FAILED,
        until=device.now + SETUP_FOR,
        error=JOIN_ERRORS[event.outcome].format(ssid=event.ssid),
    )
    return with_setup(device=device, setup=setup), ()


def join_asked(*, device: DeviceState, event: JoinAsked) -> DeviceStep:
    """
    Join the network a phone asked for, while the hotspot is up.

    Parameters
    ----------
    device : DeviceState
        Device state.
    event : JoinAsked
        The network asked for.

    Returns
    -------
    DeviceStep
        Device state joining and a JoinWifi carrying the hotspot password;
        unchanged unless the hotspot is up.
    """
    setup = device.panel.wifi
    if setup is None or setup.phase is not WifiPhase.HOTSPOT:
        return device, ()
    joining = replace(
        setup, phase=WifiPhase.JOINING, ssid=event.request.ssid,
    )
    effect = JoinWifi(request=event.request, hotspot=setup.password)
    return with_setup(device=device, setup=joining), (effect,)


def hotspot_up(*, device: DeviceState, event: HotspotStarted) -> DeviceStep:
    """
    Show the hotspot that started.

    Parameters
    ----------
    device : DeviceState
        Device state.
    event : HotspotStarted
        The networks found and the password.

    Returns
    -------
    DeviceStep
        Device state on the hotspot until `SETUP_FOR` from now; with no setup
        open any more, a StopWifiSetup; unchanged unless scanning.
    """
    setup = device.panel.wifi
    if setup is None:
        return device, (StopWifiSetup(),)
    if setup.phase is not WifiPhase.SCANNING:
        return device, ()
    setup = replace(
        setup,
        phase=WifiPhase.HOTSPOT,
        password=event.password,
        networks=event.networks,
        until=device.now + SETUP_FOR,
    )
    return with_setup(device=device, setup=setup), ()


def hotspot_failed(*, device: DeviceState) -> DeviceState:
    """
    Show that the hotspot did not start.

    Parameters
    ----------
    device : DeviceState
        Device state.

    Returns
    -------
    DeviceState
        Device state failed until `SETUP_FOR` from now; unchanged with no setup
        open.
    """
    setup = device.panel.wifi
    if setup is None:
        return device
    setup = replace(
        setup,
        phase=WifiPhase.FAILED,
        until=device.now + SETUP_FOR,
        error=START_FAILED,
    )
    return with_setup(device=device, setup=setup)


def joined(*, device: DeviceState) -> DeviceStep:
    """
    Close the setup and the menu after a join, and probe the device.

    Parameters
    ----------
    device : DeviceState
        Device state.

    Returns
    -------
    DeviceStep
        Device state with no setup or menu, marked probing, and a ProbeDevice.
    """
    panel = replace(device.panel, wifi=None, menu=None)
    return probed(device=replace(device, panel=panel))


def probed(*, device: DeviceState) -> DeviceStep:
    """
    Ask for the device's facts now that setup has let go of the radio.

    Parameters
    ----------
    device : DeviceState
        Device state.

    Returns
    -------
    DeviceStep
        Device state marked probing, and a ProbeDevice.
    """
    panel = replace(device.panel, probed_at=device.now, probing=True)
    return replace(device, panel=panel), (ProbeDevice(),)


def on_wifi_event(*, device: DeviceState, event: WifiEvent) -> DeviceStep:
    """
    Move Wi-Fi setup on after its background work answers.

    Parameters
    ----------
    device : DeviceState
        Device state.
    event : WifiEvent
        What happened.

    Returns
    -------
    DeviceStep
        New state and effects; WifiSetupStopped probes the device. The
        caller redraws.
    """
    match event:
        case HotspotStarted():
            return hotspot_up(device=device, event=event)
        case HotspotFailed():
            return hotspot_failed(device=device), ()
        case JoinAsked():
            return join_asked(device=device, event=event)
        case WifiJoined():
            return joined(device=device)
        case WifiJoinFailed():
            return join_failed(device=device, event=event)
    return probed(device=device)
