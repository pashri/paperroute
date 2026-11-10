"""Tests for Wi-Fi setup from the panel and the No Wi-Fi banner."""

from dataclasses import replace
from datetime import timedelta
from typing import Final

from paperroute_device.core.device_events import (
    ConfirmAsked,
    JoinAsked,
    PowerRequested,
)
from paperroute_device.core.device_model import (
    ConfirmAction,
    JoinOutcome,
    JoinRequest,
    MenuRow,
    PowerAction,
    WifiNetwork,
    WifiPhase,
    WifiSetup,
    WifiStatus,
)
from paperroute_device.core.kit_effects import (
    JoinWifi,
    ProbeDevice,
    StartWifiSetup,
    StopWifiSetup,
)
from paperroute_device.core.kit_events import (
    DeviceProbed,
    HotspotFailed,
    HotspotStarted,
    Tick,
    WifiJoined,
    WifiJoinFailed,
    WifiSetupStopped,
)
from paperroute_device.core.kit_state import KitState
from paperroute_device.core.panel_views import WifiSetupView
from paperroute_device.core.wifi import (
    SETUP_FOR,
    setup_started,
    wifi_setup_view,
)
from paperroute_device_testkit.device_fakes import device_facts
from tests.core.fake_app import (
    BOTH,
    DOWN,
    START,
    UP,
    ZONE,
    FakeState,
    drawn,
    kit_step,
    renders,
    run,
)
from tests.core.test_menu import opened_at, unmoved

PASSWORD: Final[str] = '7Q4K9W2M'
HOME: Final[WifiNetwork] = WifiNetwork(
    ssid='Home', signal=80, secured=True, enterprise=False,
)
STARTED: Final[HotspotStarted] = HotspotStarted(
    networks=(HOME,), password=PASSWORD,
)
REQUEST: Final[JoinRequest] = JoinRequest(ssid='Home', password='hunter22')
NO_WIFI: Final[str] = (
    'No Wi-Fi · cached times · hold both knobs, then Wi-Fi'
)


def hotspot() -> KitState[FakeState]:
    """
    Open Wi-Fi setup from the menu and start its hotspot at START.

    Returns
    -------
    KitState[FakeState]
        Kit state with the hotspot up.
    """
    kit, _ = run(kit=opened_at(row=MenuRow.WIFI), events=(DOWN, STARTED))
    return kit


def test_wifi_row_scans_then_shows_hotspot() -> None:
    """The Wi-Fi row scans first, then shows the hotspot's code."""
    kit, effects = kit_step(kit=opened_at(row=MenuRow.WIFI), event=DOWN)
    assert StartWifiSetup() in effects
    assert kit.device.panel.wifi == WifiSetup(phase=WifiPhase.SCANNING)
    assert kit.device.panel.menu is not None
    kit, effects = kit_step(kit=kit, event=STARTED)
    view = renders(effects=effects)[0]
    assert isinstance(view, WifiSetupView)
    assert view.phase is WifiPhase.HOTSPOT
    assert view.qr == 'WIFI:T:WPA;S:paperroute-setup;P:7Q4K9W2M;;'
    assert view.password == '7Q4K 9W2M'
    assert view.minutes_left == 10
    assert kit.device.panel.wifi is not None
    assert kit.device.panel.wifi.networks == (HOME,)


def test_second_start_ignored() -> None:
    """While a setup is open the Wi-Fi row is not reachable to restart."""
    assert unmoved(kit=hotspot(), event=BOTH)


def test_setup_closes_after_ten_minutes() -> None:
    """The hotspot stops 10 minutes after it started."""
    late = START + SETUP_FOR - timedelta(seconds=1)
    kit, effects = kit_step(kit=hotspot(), event=Tick(now=late))
    assert StopWifiSetup() not in effects
    assert kit.device.panel.wifi is not None
    kit, effects = kit_step(kit=kit, event=Tick(now=START + SETUP_FOR))
    assert StopWifiSetup() in effects
    assert kit.device.panel.wifi is None


def test_failed_setup_still_times_out() -> None:
    """A hotspot that would not start shows why and closes in time."""
    kit, _ = run(
        kit=opened_at(row=MenuRow.WIFI), events=(DOWN, HotspotFailed()),
    )
    assert kit.device.panel.wifi is not None
    assert kit.device.panel.wifi.phase is WifiPhase.FAILED
    assert kit.device.panel.wifi.error == "Couldn't start Wi-Fi setup"
    kit, effects = kit_step(kit=kit, event=Tick(now=START + SETUP_FOR))
    assert StopWifiSetup() in effects
    assert kit.device.panel.wifi is None


def test_join_success_closes_setup_and_probes() -> None:
    """A join from the phone stops the hotspot and joins; then it closes."""
    kit, effects = kit_step(kit=hotspot(), event=JoinAsked(request=REQUEST))
    assert JoinWifi(request=REQUEST, hotspot=PASSWORD) in effects
    view = wifi_setup_view(device=kit.device, zone=ZONE)
    assert view.phase is WifiPhase.JOINING
    assert view.ssid == 'Home'
    kit, effects = kit_step(kit=kit, event=WifiJoined(ssid='Home'))
    assert kit.device.panel.wifi is None
    assert kit.device.panel.menu is None
    assert ProbeDevice() in effects


def test_wrong_password_brings_setup_back() -> None:
    """A wrong password shows the hotspot again with the reason."""
    kit, _ = kit_step(kit=hotspot(), event=JoinAsked(request=REQUEST))
    later = START + timedelta(minutes=8)
    kit, _ = kit_step(kit=kit, event=Tick(now=later))
    failed = WifiJoinFailed(
        ssid='Home', outcome=JoinOutcome.WRONG_PASSWORD, hotspot=True,
    )
    kit, effects = kit_step(kit=kit, event=failed)
    view = renders(effects=effects)[0]
    assert isinstance(view, WifiSetupView)
    assert view.phase is WifiPhase.HOTSPOT
    assert view.error == "Couldn't join Home: wrong password"
    assert kit.device.panel.wifi is not None
    assert kit.device.panel.wifi.until == later + SETUP_FOR


def test_failed_join_without_hotspot_shows_failed() -> None:
    """A join that failed and could not bring the hotspot back is FAILED."""
    kit, _ = kit_step(kit=hotspot(), event=JoinAsked(request=REQUEST))
    failed = WifiJoinFailed(
        ssid='Home', outcome=JoinOutcome.NOT_FOUND, hotspot=False,
    )
    kit, _ = kit_step(kit=kit, event=failed)
    assert kit.device.panel.wifi is not None
    assert kit.device.panel.wifi.phase is WifiPhase.FAILED
    assert kit.device.panel.wifi.error == "Couldn't join Home: not found"


def test_top_press_cancels_setup() -> None:
    """A top press stops the hotspot and goes back to the menu."""
    kit, effects = kit_step(kit=hotspot(), event=UP)
    assert StopWifiSetup() in effects
    assert kit.device.panel.wifi is None
    assert kit.device.panel.menu is not None


def test_scanning_and_joining_ignore_knobs() -> None:
    """Knobs do nothing while scanning or joining."""
    scanning, _ = kit_step(kit=opened_at(row=MenuRow.WIFI), event=DOWN)
    joining, _ = kit_step(kit=hotspot(), event=JoinAsked(request=REQUEST))
    for kit in (scanning, joining):
        for event in (UP, DOWN, BOTH):
            assert unmoved(kit=kit, event=event)


def test_join_ignored_unless_hotspot() -> None:
    """A join asked for with no hotspot up does nothing."""
    scanning, _ = kit_step(kit=opened_at(row=MenuRow.WIFI), event=DOWN)
    for kit in (drawn(), scanning):
        assert unmoved(kit=kit, event=JoinAsked(request=REQUEST))


def test_phone_join_wifi_confirm_starts_setup() -> None:
    """Accepting a phone's Join another network starts Wi-Fi setup."""
    asked = ConfirmAsked(action=ConfirmAction.JOIN_WIFI)
    kit, _ = kit_step(kit=drawn(), event=asked)
    kit, effects = kit_step(kit=kit, event=DOWN)
    assert kit.device.panel.confirm is None
    assert StartWifiSetup() in effects
    assert kit.device.panel.wifi == WifiSetup(phase=WifiPhase.SCANNING)


def test_no_wifi_banner() -> None:
    """Managed with no network, the live view carries the No Wi-Fi line."""
    facts = replace(device_facts(), wifi=WifiStatus(managed=True))
    kit, effects = kit_step(kit=drawn(), event=DeviceProbed(facts=facts))
    assert renders(effects=effects) == [f'view:live {NO_WIFI}']
    assert kit.device.last_view == f'view:live {NO_WIFI}'


def test_no_banner_when_unmanaged() -> None:
    """Without NetworkManager the banner never shows."""
    kit, effects = kit_step(
        kit=drawn(), event=DeviceProbed(facts=device_facts()),
    )
    assert not effects
    assert kit.device.last_view == 'view:live'


def test_wifi_reprs_hide_passwords() -> None:
    """No repr carries the network's or the hotspot's password."""
    kit, _ = kit_step(kit=hotspot(), event=JoinAsked(request=REQUEST))
    things = (
        REQUEST,
        JoinWifi(request=REQUEST, hotspot=PASSWORD),
        STARTED,
        kit.device.panel.wifi,
        wifi_setup_view(device=hotspot().device, zone=ZONE),
    )
    for thing in things:
        assert 'hunter22' not in repr(thing)
        assert '7Q4K' not in repr(thing)


def test_power_and_reset_stop_hotspot() -> None:
    """Shutting down or resetting with setup open stops the hotspot."""
    off = PowerRequested(action=PowerAction.OFF)
    kit, effects = kit_step(kit=hotspot(), event=off)
    assert StopWifiSetup() in effects
    assert kit_step(kit=kit, event=DOWN)[0].device.panel.wifi is None
    reset = ConfirmAsked(action=ConfirmAction.RESET_SETTINGS)
    kit, effects = run(kit=hotspot(), events=(reset, DOWN))
    assert StopWifiSetup() in effects
    assert kit.device.panel.wifi is None


def test_late_hotspot_is_stopped() -> None:
    """A hotspot that comes up after setup closed is taken down."""
    kit, _ = kit_step(kit=opened_at(row=MenuRow.WIFI), event=DOWN)
    kit, _ = kit_step(kit=kit, event=PowerRequested(action=PowerAction.OFF))
    _, effects = kit_step(kit=kit, event=STARTED)
    assert StopWifiSetup() in effects
    back = WifiJoinFailed(
        ssid='Home', outcome=JoinOutcome.FAILED, hotspot=True,
    )
    _, effects = kit_step(kit=drawn(), event=back)
    assert StopWifiSetup() in effects
    assert unmoved(kit=drawn(), event=HotspotFailed())


def test_stopped_setup_probes() -> None:
    """Once the hotspot is down the device's facts are asked for again."""
    kit, _ = kit_step(kit=hotspot(), event=UP)
    kit, effects = kit_step(kit=kit, event=WifiSetupStopped())
    assert ProbeDevice() in effects
    assert kit.device.panel.probing


def test_no_probe_while_setup_open() -> None:
    """The radio is not probed while setup drives it."""
    kit = hotspot()
    for minutes in range(1, 4):
        tick = Tick(now=START + timedelta(minutes=minutes))
        kit, effects = kit_step(kit=kit, event=tick)
        assert ProbeDevice() not in effects


def test_setup_start_and_hotspot_need_their_phase() -> None:
    """Setup does not start twice; a hotspot only answers a scan."""
    kit = hotspot()
    device, effects = setup_started(device=kit.device)
    assert device is kit.device
    assert not effects
    assert unmoved(kit=kit, event=STARTED)
