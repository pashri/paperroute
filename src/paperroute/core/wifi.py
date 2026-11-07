"""Wi-Fi setup from the panel."""

import math
from dataclasses import replace
from datetime import timedelta
from typing import Final

from paperroute.core.device_model import JoinOutcome, WifiPhase, WifiSetup
from paperroute.core.messages import (
    Effect,
    HotspotFailed,
    HotspotStarted,
    JoinAsked,
    JoinWifi,
    ProbeDevice,
    StartWifiSetup,
    StopWifiSetup,
    WifiEvent,
    WifiJoined,
    WifiJoinFailed,
)
from paperroute.core.model import SYDNEY
from paperroute.core.panel_views import WifiSetupView
from paperroute.core.state_model import State
from paperroute_device.core.text import HINT_DOT, clock_text
from paperroute_hardware.core.knob_events import Knob, KnobPressed

SETUP_FOR: Final[timedelta] = timedelta(minutes=10)
SETUP_SSID: Final[str] = 'paperroute-setup'
SETUP_ADDRESS: Final[str] = '10.42.0.1'
WIFI_SPECIAL: Final[str] = '\\;,:"'
JOIN_ERRORS: Final[dict[JoinOutcome, str]] = {
    JoinOutcome.WRONG_PASSWORD: "Couldn't join {ssid}: wrong password",
    JoinOutcome.NOT_FOUND: "Couldn't join {ssid}: not found",
    JoinOutcome.FAILED: "Couldn't join {ssid}",
}
START_FAILED: Final[str] = "Couldn't start Wi-Fi setup"
SETUP_STEPS: Final[tuple[str, ...]] = (
    f'1. Scan the code with your phone. It joins the network {SETUP_SSID}.',
    f'2. Open {SETUP_ADDRESS}/setup, pick your Wi-Fi and type its password.',
    '3. paperroute connects and this screen closes.',
)
SCANNING_LINE: Final[str] = 'Looking for Wi-Fi networks...'
OPEN_PHASES: Final[frozenset[WifiPhase]] = frozenset(
    {WifiPhase.HOTSPOT, WifiPhase.FAILED},
)
TOP_PRESS: Final[KnobPressed] = KnobPressed(knob=Knob.TOP)

type Result = tuple[State, tuple[Effect, ...]]


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


def no_camera_line(*, password: str) -> str:
    """
    Say how to join the hotspot without scanning the code.

    Parameters
    ----------
    password : str
        The password as shown.

    Returns
    -------
    str
        The line.
    """
    return (
        f'No camera? Join {SETUP_SSID}, password {password},'
        f' open {SETUP_ADDRESS}/setup'
    )


def setup_footer(*, minutes_left: int) -> str:
    """
    Say how long the hotspot stays up and how to cancel.

    Parameters
    ----------
    minutes_left : int
        Whole minutes until it closes.

    Returns
    -------
    str
        The footer.
    """
    return (
        f'New password each time{HINT_DOT}closes in {minutes_left} min'
        f'{HINT_DOT}Top: press to cancel'
    )


def joining_line(*, ssid: str | None) -> str:
    """
    Say which network is being joined.

    Parameters
    ----------
    ssid : str | None
        The network, or None.

    Returns
    -------
    str
        The line.
    """
    return f'Joining {ssid or "the network"}...'


def wifi_setup_view(*, state: State) -> WifiSetupView:
    """
    Build the Wi-Fi setup screen.

    Parameters
    ----------
    state : State
        State, with a setup open.

    Returns
    -------
    WifiSetupView
        The phase, the code, the password in groups of four and the
        whole minutes left, rounded up.
    """
    setup = state.panel.wifi or WifiSetup(phase=WifiPhase.SCANNING)
    seconds = 0.0
    if setup.until is not None:
        seconds = max((setup.until - state.now).total_seconds(), 0.0)
    return WifiSetupView(
        clock=clock_text(moment=state.now, zone=SYDNEY),
        phase=setup.phase,
        qr=wifi_qr(password=setup.password),
        password=shown_password(password=setup.password),
        address=SETUP_ADDRESS,
        minutes_left=math.ceil(seconds / 60),
        ssid=setup.ssid,
        error=setup.error,
    )


def with_setup(*, state: State, setup: WifiSetup | None) -> State:
    """
    Set the open Wi-Fi setup.

    Parameters
    ----------
    state : State
        State.
    setup : WifiSetup | None
        The setup, or None to close it.

    Returns
    -------
    State
        State with the setup set.
    """
    return replace(state, panel=replace(state.panel, wifi=setup))


def setup_started(*, state: State) -> Result:
    """
    Open Wi-Fi setup and scan, keeping the menu under it.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    Result
        State scanning and a StartWifiSetup; unchanged while a setup is
        open or a power action or reset is under way. The caller
        redraws.
    """
    busy = state.power is not None or state.panel.resetting is not None
    if busy or state.panel.wifi is not None:
        return state, ()
    setup = WifiSetup(phase=WifiPhase.SCANNING)
    return with_setup(state=state, setup=setup), (StartWifiSetup(),)


def setup_closed(*, state: State) -> Result:
    """
    Close an open Wi-Fi setup and take its hotspot down.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    Result
        State with no setup and a StopWifiSetup; unchanged with none
        open.
    """
    if state.panel.wifi is None:
        return state, ()
    return with_setup(state=state, setup=None), (StopWifiSetup(),)


def setup_knob(*, state: State, event: object) -> Result:
    """
    Apply a touch to the open Wi-Fi setup.

    Parameters
    ----------
    state : State
        State, with a setup open.
    event : object
        The touch.

    Returns
    -------
    Result
        A top press with the hotspot up or failed closes it; anything
        else, and every touch while scanning or joining, does nothing.
    """
    setup = state.panel.wifi
    if setup is None or setup.phase not in OPEN_PHASES:
        return state, ()
    if event != TOP_PRESS:
        return state, ()
    return setup_closed(state=state)


def setup_expired(*, state: State) -> Result:
    """
    Close a hotspot that has been up for `SETUP_FOR`.

    Parameters
    ----------
    state : State
        State at the tick's time.

    Returns
    -------
    Result
        State with the setup closed and a StopWifiSetup once the
        hotspot or failed setup reaches its time; unchanged otherwise.
    """
    setup = state.panel.wifi
    if setup is None or setup.phase not in OPEN_PHASES:
        return state, ()
    if setup.until is None or state.now < setup.until:
        return state, ()
    return setup_closed(state=state)


def join_failed(*, state: State, event: WifiJoinFailed) -> Result:
    """
    Show the setup again after a failed join.

    Parameters
    ----------
    state : State
        State.
    event : WifiJoinFailed
        How the join failed.

    Returns
    -------
    Result
        State back on the hotspot, or failed when the hotspot did not
        come back, with the reason and a fresh `SETUP_FOR`; with no
        setup open any more, a StopWifiSetup for a hotspot back up.
    """
    setup = state.panel.wifi
    if setup is None:
        return state, (StopWifiSetup(),) if event.hotspot else ()
    setup = replace(
        setup,
        phase=WifiPhase.HOTSPOT if event.hotspot else WifiPhase.FAILED,
        until=state.now + SETUP_FOR,
        error=JOIN_ERRORS[event.outcome].format(ssid=event.ssid),
    )
    return with_setup(state=state, setup=setup), ()


def join_asked(*, state: State, event: JoinAsked) -> Result:
    """
    Join the network a phone asked for, while the hotspot is up.

    Parameters
    ----------
    state : State
        State.
    event : JoinAsked
        The network asked for.

    Returns
    -------
    Result
        State joining and a JoinWifi carrying the hotspot password;
        unchanged unless the hotspot is up.
    """
    setup = state.panel.wifi
    if setup is None or setup.phase is not WifiPhase.HOTSPOT:
        return state, ()
    joining = replace(
        setup, phase=WifiPhase.JOINING, ssid=event.request.ssid,
    )
    effect = JoinWifi(request=event.request, hotspot=setup.password)
    return with_setup(state=state, setup=joining), (effect,)


def hotspot_up(*, state: State, event: HotspotStarted) -> Result:
    """
    Show the hotspot that started.

    Parameters
    ----------
    state : State
        State.
    event : HotspotStarted
        The networks found and the password.

    Returns
    -------
    Result
        State on the hotspot until `SETUP_FOR` from now; with no setup
        open any more, a StopWifiSetup; unchanged unless scanning.
    """
    setup = state.panel.wifi
    if setup is None:
        return state, (StopWifiSetup(),)
    if setup.phase is not WifiPhase.SCANNING:
        return state, ()
    setup = replace(
        setup,
        phase=WifiPhase.HOTSPOT,
        password=event.password,
        networks=event.networks,
        until=state.now + SETUP_FOR,
    )
    return with_setup(state=state, setup=setup), ()


def hotspot_failed(*, state: State) -> State:
    """
    Show that the hotspot did not start.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    State
        State failed until `SETUP_FOR` from now; unchanged with no setup
        open.
    """
    setup = state.panel.wifi
    if setup is None:
        return state
    setup = replace(
        setup,
        phase=WifiPhase.FAILED,
        until=state.now + SETUP_FOR,
        error=START_FAILED,
    )
    return with_setup(state=state, setup=setup)


def joined(*, state: State) -> Result:
    """
    Close the setup and the menu after a join, and probe the device.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    Result
        State with no setup or menu, marked probing, and a ProbeDevice.
    """
    panel = replace(state.panel, wifi=None, menu=None)
    return probed(state=replace(state, panel=panel))


def probed(*, state: State) -> Result:
    """
    Ask for the device's facts now that setup has let go of the radio.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    Result
        State marked probing, and a ProbeDevice.
    """
    panel = replace(state.panel, probed_at=state.now, probing=True)
    return replace(state, panel=panel), (ProbeDevice(),)


def on_wifi_event(*, state: State, event: WifiEvent) -> Result:
    """
    Move Wi-Fi setup on after its background work answers.

    Parameters
    ----------
    state : State
        State.
    event : WifiEvent
        What happened.

    Returns
    -------
    Result
        New state and effects; WifiSetupStopped probes the device. The
        caller redraws.
    """
    match event:
        case HotspotStarted():
            return hotspot_up(state=state, event=event)
        case HotspotFailed():
            return hotspot_failed(state=state), ()
        case JoinAsked():
            return join_asked(state=state, event=event)
        case WifiJoined():
            return joined(state=state)
        case WifiJoinFailed():
            return join_failed(state=state, event=event)
    return probed(state=state)
