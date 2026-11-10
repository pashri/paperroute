"""Tests for the banner line's device warnings and their rank."""

from dataclasses import replace

from paperroute_device.core.banners import (
    banner_line,
    device_banners,
    no_wifi_banner,
    offline,
)
from paperroute_device.core.device_model import (
    Panel,
    WifiPhase,
    WifiSetup,
    WifiStatus,
)
from paperroute_device.core.device_state import Banner, DeviceState
from paperroute_device_testkit.device_fakes import device_facts
from tests.core.fake_app import APP, START, FakeState


def test_offline_line_with_and_without_phrase() -> None:
    """The No Wi-Fi line names the phrase, or leaves it and its dot out."""
    assert no_wifi_banner(phrase='cached times') == (
        'No Wi-Fi · cached times · hold both knobs, then Wi-Fi'
    )
    assert no_wifi_banner(phrase='') == (
        'No Wi-Fi · hold both knobs, then Wi-Fi'
    )
    facts = replace(device_facts(), wifi=WifiStatus(managed=True))
    assert not offline(panel=Panel())
    assert not offline(panel=Panel(facts=device_facts()))
    assert offline(panel=Panel(facts=facts))
    setup = WifiSetup(phase=WifiPhase.SCANNING)
    assert not offline(panel=Panel(facts=facts, wifi=setup))
    joined = replace(facts, wifi=WifiStatus(managed=True, ssid='Home'))
    assert not offline(panel=Panel(facts=joined))


def test_device_banners_rank_before_app_banners() -> None:
    """No Wi-Fi, then the first missing key, then the app's own lines."""
    facts = replace(device_facts(), wifi=WifiStatus(managed=True))
    panel = Panel(facts=facts, missing_keys=('First', 'Second'))
    lines = device_banners(panel=panel, phrase='')
    assert lines == (
        Banner(text='No Wi-Fi · hold both knobs, then Wi-Fi'),
        Banner(text='No First key · set it on your phone'),
    )
    state = FakeState(banners=(Banner(text='App', warning=False),))
    device = DeviceState(now=START)
    line = banner_line(panel=panel, app=APP, state=state, device=device)
    assert line == Banner(
        text='No Wi-Fi · cached times · hold both knobs, then Wi-Fi',
    )
    keyed = Panel(missing_keys=('First',))
    line = banner_line(panel=keyed, app=APP, state=state, device=device)
    assert line == lines[1]
    line = banner_line(panel=Panel(), app=APP, state=state, device=device)
    assert line == Banner(text='App', warning=False)
    empty = banner_line(
        panel=Panel(), app=APP, state=FakeState(), device=device,
    )
    assert empty is None
