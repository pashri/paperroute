"""Tests for Wi-Fi setup from the panel and the No Wi-Fi banner."""

from dataclasses import replace
from datetime import timedelta
from typing import Final

from paperroute.core.banners import NO_WIFI
from paperroute.core.device_model import (
    ConfirmAction,
    JoinOutcome,
    JoinRequest,
    MenuRow,
    WifiNetwork,
    WifiPhase,
    WifiSetup,
    WifiStatus,
)
from paperroute.core.messages import (
    ConfirmAsked,
    DeviceProbed,
    FetchFeed,
    HotspotFailed,
    HotspotStarted,
    JoinAsked,
    JoinWifi,
    PowerAction,
    PowerRequested,
    ProbeDevice,
    RebuildTimetable,
    StartWifiSetup,
    StopWifiSetup,
    Tick,
    WifiJoined,
    WifiJoinFailed,
    WifiSetupStopped,
)
from paperroute.core.panel_views import WifiSetupView
from paperroute.core.state import step
from paperroute.core.state_model import State
from paperroute.core.state_views import stop_view_of
from paperroute.core.wifi import SETUP_FOR, wifi_setup_view
from tests.core.factories import START
from tests.core.test_menu import BOTH, DOWN, UP, opened_at, renders, run
from tests.core.test_rebuild import pinned
from tests.core.test_state import fresh, loaded
from tests.fakes import device_facts

PASSWORD: Final[str] = '7Q4K9W2M'
HOME: Final[WifiNetwork] = WifiNetwork(
    ssid='Home', signal=80, secured=True, enterprise=False,
)
STARTED: Final[HotspotStarted] = HotspotStarted(
    networks=(HOME,), password=PASSWORD,
)
REQUEST: Final[JoinRequest] = JoinRequest(ssid='Home', password='hunter22')


def hotspot() -> State:
    """
    Open Wi-Fi setup from the menu and start its hotspot at START.

    Returns
    -------
    State
        State with the hotspot up.
    """
    state, _ = run(state=opened_at(row=MenuRow.WIFI), events=(DOWN, STARTED))
    return state


def test_wifi_row_scans_then_shows_hotspot() -> None:
    """The Wi-Fi row scans first, then shows the hotspot's code."""
    state, effects = step(opened_at(row=MenuRow.WIFI), DOWN)
    assert StartWifiSetup() in effects
    assert state.panel.wifi == WifiSetup(phase=WifiPhase.SCANNING)
    assert state.panel.menu is not None
    state, effects = step(state, STARTED)
    view = renders(effects=effects)[0]
    assert isinstance(view, WifiSetupView)
    assert view.phase is WifiPhase.HOTSPOT
    assert view.qr == 'WIFI:T:WPA;S:paperroute-setup;P:7Q4K9W2M;;'
    assert view.password == '7Q4K 9W2M'
    assert view.minutes_left == 10
    assert state.panel.wifi is not None
    assert state.panel.wifi.networks == (HOME,)


def test_second_start_ignored() -> None:
    """While a setup is open the Wi-Fi row is not reachable to restart."""
    state = hotspot()
    assert step(state, BOTH) == (state, ())


def test_setup_closes_after_ten_minutes() -> None:
    """The hotspot stops 10 minutes after it started."""
    state = hotspot()
    late = START + SETUP_FOR - timedelta(seconds=1)
    state, effects = step(state, Tick(now=late))
    assert StopWifiSetup() not in effects
    assert state.panel.wifi is not None
    state, effects = step(state, Tick(now=START + SETUP_FOR))
    assert StopWifiSetup() in effects
    assert state.panel.wifi is None


def test_failed_setup_still_times_out() -> None:
    """A hotspot that would not start shows why and closes in time."""
    state, _ = run(
        state=opened_at(row=MenuRow.WIFI), events=(DOWN, HotspotFailed()),
    )
    assert state.panel.wifi is not None
    assert state.panel.wifi.phase is WifiPhase.FAILED
    assert state.panel.wifi.error == "Couldn't start Wi-Fi setup"
    state, effects = step(state, Tick(now=START + SETUP_FOR))
    assert StopWifiSetup() in effects
    assert state.panel.wifi is None


def test_join_success_closes_setup_and_probes() -> None:
    """A join from the phone stops the hotspot and joins; then it closes."""
    state, effects = step(hotspot(), JoinAsked(request=REQUEST))
    assert JoinWifi(request=REQUEST, hotspot=PASSWORD) in effects
    view = wifi_setup_view(state=state)
    assert view.phase is WifiPhase.JOINING
    assert view.ssid == 'Home'
    state, effects = step(state, WifiJoined(ssid='Home'))
    assert state.panel.wifi is None
    assert state.panel.menu is None
    assert ProbeDevice() in effects


def test_wrong_password_brings_setup_back() -> None:
    """A wrong password shows the hotspot again with the reason."""
    state, _ = step(hotspot(), JoinAsked(request=REQUEST))
    later = START + timedelta(minutes=8)
    state, _ = step(state, Tick(now=later))
    failed = WifiJoinFailed(
        ssid='Home', outcome=JoinOutcome.WRONG_PASSWORD, hotspot=True,
    )
    state, effects = step(state, failed)
    view = renders(effects=effects)[0]
    assert isinstance(view, WifiSetupView)
    assert view.phase is WifiPhase.HOTSPOT
    assert view.error == "Couldn't join Home: wrong password"
    assert state.panel.wifi is not None
    assert state.panel.wifi.until == later + SETUP_FOR


def test_failed_join_without_hotspot_shows_failed() -> None:
    """A join that failed and could not bring the hotspot back is FAILED."""
    state, _ = step(hotspot(), JoinAsked(request=REQUEST))
    failed = WifiJoinFailed(
        ssid='Home', outcome=JoinOutcome.NOT_FOUND, hotspot=False,
    )
    state, _ = step(state, failed)
    assert state.panel.wifi is not None
    assert state.panel.wifi.phase is WifiPhase.FAILED
    assert state.panel.wifi.error == "Couldn't join Home: not found"


def test_top_press_cancels_setup() -> None:
    """A top press stops the hotspot and goes back to the menu."""
    state, effects = step(hotspot(), UP)
    assert StopWifiSetup() in effects
    assert state.panel.wifi is None
    assert state.panel.menu is not None


def test_scanning_and_joining_ignore_knobs() -> None:
    """Knobs do nothing while scanning or joining."""
    scanning, _ = step(opened_at(row=MenuRow.WIFI), DOWN)
    joining, _ = step(hotspot(), JoinAsked(request=REQUEST))
    for state in (scanning, joining):
        for event in (UP, DOWN, BOTH):
            assert step(state, event) == (state, ())


def test_join_ignored_unless_hotspot() -> None:
    """A join asked for with no hotspot up does nothing."""
    scanning, _ = step(opened_at(row=MenuRow.WIFI), DOWN)
    for state in (pinned(), scanning):
        assert step(state, JoinAsked(request=REQUEST)) == (state, ())


def test_phone_join_wifi_confirm_starts_setup() -> None:
    """Accepting a phone's Join another network starts Wi-Fi setup."""
    asked = ConfirmAsked(action=ConfirmAction.JOIN_WIFI)
    state, _ = step(pinned(), asked)
    state, effects = step(state, DOWN)
    assert state.panel.confirm is None
    assert StartWifiSetup() in effects
    assert state.panel.wifi == WifiSetup(phase=WifiPhase.SCANNING)


def offline_state() -> State:
    """
    Give a loaded state whose facts say no Wi-Fi is joined.

    Returns
    -------
    State
        The state.
    """
    facts = replace(device_facts(), wifi=WifiStatus(managed=True))
    state, _ = step(loaded(state=fresh()), DeviceProbed(facts=facts))
    return state


def test_no_wifi_banner() -> None:
    """Managed with no network: banner, no polling, no auto rebuild."""
    state = offline_state()
    view = stop_view_of(state=state)
    assert view.banner == NO_WIFI
    assert view.warning
    for seconds in range(30, 150, 30):
        tick = Tick(now=START + timedelta(seconds=seconds))
        state, effects = step(state, tick)
        assert not any(isinstance(e, FetchFeed) for e in effects)
        assert not any(isinstance(e, RebuildTimetable) for e in effects)


def test_no_banner_when_unmanaged() -> None:
    """Without NetworkManager the banner never shows."""
    state, _ = step(loaded(state=fresh()), DeviceProbed(facts=device_facts()))
    view = stop_view_of(state=state)
    assert view.banner != NO_WIFI
    assert not view.warning


def test_wifi_reprs_hide_passwords() -> None:
    """No repr carries the network's or the hotspot's password."""
    state, _ = step(hotspot(), JoinAsked(request=REQUEST))
    things = (
        REQUEST,
        JoinWifi(request=REQUEST, hotspot=PASSWORD),
        STARTED,
        state.panel.wifi,
        wifi_setup_view(state=hotspot()),
    )
    for thing in things:
        assert 'hunter22' not in repr(thing)
        assert '7Q4K' not in repr(thing)


def test_power_and_reset_stop_hotspot() -> None:
    """Shutting down or resetting with setup open stops the hotspot."""
    off = PowerRequested(action=PowerAction.OFF)
    state, effects = step(hotspot(), off)
    assert StopWifiSetup() in effects
    assert step(state, DOWN)[0].panel.wifi is None
    reset = ConfirmAsked(action=ConfirmAction.RESET_SETTINGS)
    state, effects = run(state=hotspot(), events=(reset, DOWN))
    assert StopWifiSetup() in effects
    assert state.panel.wifi is None


def test_late_hotspot_is_stopped() -> None:
    """A hotspot that comes up after setup closed is taken down."""
    state, _ = step(opened_at(row=MenuRow.WIFI), DOWN)
    state, _ = step(state, PowerRequested(action=PowerAction.OFF))
    _, effects = step(state, STARTED)
    assert StopWifiSetup() in effects
    back = WifiJoinFailed(
        ssid='Home', outcome=JoinOutcome.FAILED, hotspot=True,
    )
    _, effects = step(pinned(), back)
    assert StopWifiSetup() in effects
    assert step(pinned(), HotspotFailed()) == (pinned(), ())


def test_stopped_setup_probes() -> None:
    """Once the hotspot is down the device's facts are asked for again."""
    state, _ = step(hotspot(), UP)
    state, effects = step(state, WifiSetupStopped())
    assert ProbeDevice() in effects
    assert state.panel.probing


def test_no_probe_while_setup_open() -> None:
    """The radio is not probed while setup drives it."""
    state = hotspot()
    for minutes in range(1, 4):
        tick = Tick(now=START + timedelta(minutes=minutes))
        state, effects = step(state, tick)
        assert ProbeDevice() not in effects
