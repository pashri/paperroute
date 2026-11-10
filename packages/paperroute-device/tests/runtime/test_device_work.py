"""Tests for carrying out the device's effects."""

import asyncio
from dataclasses import replace
from pathlib import Path
from typing import Final

import pytest

from paperroute_device.adapters.resets import FileResets
from paperroute_device.core.device_model import (
    DeviceStatus,
    JoinOutcome,
    JoinRequest,
    ResetKind,
    WifiNetwork,
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
from paperroute_device.runtime import device_work as work_module
from paperroute_device.runtime.device_work import (
    DevicePorts,
    Reported,
    SetupEffect,
    device_work,
    leftover_stopped,
    report_status,
)
from paperroute_device.runtime.timings import KitTimings
from paperroute_device_testkit.device_fakes import (
    FakeKeys,
    FakeProbe,
    FakeWifi,
    HangingProbe,
    RecordingResets,
    device_facts,
)
from tests.core.fake_app import FakeApp, fresh, kit_step

HOME: Final[WifiNetwork] = WifiNetwork(
    ssid='Home', signal=80, secured=True, enterprise=False,
)
REQUEST: Final[JoinRequest] = JoinRequest(ssid='Home', password='hunter22')


def drained(*, queue: asyncio.Queue[object]) -> list[object]:
    """
    Take every event waiting on a queue.

    Parameters
    ----------
    queue : asyncio.Queue[object]
        The queue.

    Returns
    -------
    list[object]
        The events, in order.
    """
    posted: list[object] = []
    while not queue.empty():
        posted.append(queue.get_nowait())
    return posted


async def worked(*, ports: DevicePorts) -> tuple[list[object], object]:
    """
    Run a probe effect and give what it posted and its fallback.

    Parameters
    ----------
    ports : DevicePorts
        Device adapters.

    Returns
    -------
    tuple[list[object], object]
        The events posted, and the event posted if the work crashes.
    """
    queue: asyncio.Queue[object] = asyncio.Queue()
    work, fallback = device_work(
        ports=ports, app=FakeApp(), effect=ProbeDevice(), queue=queue,
    )
    await work
    return drained(queue=queue), fallback


async def test_probe_effect_posts_facts() -> None:
    """A probe posts the facts it found, with the app's part."""
    posted, fallback = await worked(ports=DevicePorts(probe=FakeProbe()))
    assert posted == [DeviceProbed(facts=replace(device_facts(), app='probed'))]
    assert fallback == DeviceProbeFailed()


async def test_probe_crash_posts_failed() -> None:
    """A probe that raises posts DeviceProbeFailed."""
    probe = FakeProbe(error=OSError('gone'))
    posted, _ = await worked(ports=DevicePorts(probe=probe))
    assert posted == [DeviceProbeFailed()]


async def test_no_probe_posts_failed() -> None:
    """With no probe the effect posts DeviceProbeFailed."""
    posted, _ = await worked(ports=DevicePorts())
    assert posted == [DeviceProbeFailed()]


async def test_hanging_probe_posts_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A probe that takes too long posts DeviceProbeFailed."""
    monkeypatch.setattr(work_module, 'PROBE_TIMEOUT', 0.01)
    posted, _ = await worked(ports=DevicePorts(probe=HangingProbe()))
    assert posted == [DeviceProbeFailed()]


async def test_probe_waits_for_the_radio() -> None:
    """A probe does not read the device while the radio is held."""
    ports = DevicePorts(probe=FakeProbe())
    queue: asyncio.Queue[object] = asyncio.Queue()
    async with ports.radio:
        work, _ = device_work(
            ports=ports, app=FakeApp(), effect=ProbeDevice(), queue=queue,
        )
        task = asyncio.ensure_future(work)
        for _ in range(5):
            await asyncio.sleep(0)
        assert queue.empty()
    await task
    assert queue.get_nowait() == DeviceProbed(
        facts=replace(device_facts(), app='probed'),
    )


def test_status_ignores_changing_readings() -> None:
    """Probes differing only in uptime, CPU, memory and disk report once."""
    timings = KitTimings()
    reported = Reported()
    kit = fresh()
    first = device_facts()
    second = replace(
        first, uptime_s=99.0, cpu_c=50.5, mem_free=1, disk_free=2,
    )
    seen: list[DeviceStatus | None] = []
    for facts in (first, second):
        kit, _ = kit_step(kit=kit, event=DeviceProbed(facts=facts))
        report_status(observer=timings, kit=kit, reported=reported)
        seen.append(timings.device)
        timings.device = None
    assert seen[0] is not None
    assert seen[1] is None


def test_unchanged_probe_does_not_redraw() -> None:
    """A probe giving the same facts again draws nothing."""
    kit, _ = kit_step(kit=fresh(), event=DeviceProbed(facts=device_facts()))
    _, effects = kit_step(kit=kit, event=DeviceProbed(facts=device_facts()))
    assert not effects


async def test_reset_work_posts_done() -> None:
    """A reset runs the resets, then posts that it is done."""
    resets = RecordingResets()
    queue: asyncio.Queue[object] = asyncio.Queue()
    work, fallback = device_work(
        ports=DevicePorts(resets=resets),
        app=FakeApp(),
        effect=ResetDevice(kind=ResetKind.ERASE),
        queue=queue,
    )
    await work
    assert resets.kinds == [ResetKind.ERASE]
    assert queue.get_nowait() == ResetDone(kind=ResetKind.ERASE)
    assert fallback == ResetDone(kind=ResetKind.ERASE)


async def test_reset_erases_what_the_app_names(tmp_path: Path) -> None:
    """Erase everything deletes the files the app names."""
    data = tmp_path / 'saved.db'
    data.write_text('x', encoding='utf-8')
    resets = FileResets(
        data_dir=tmp_path,
        state=tmp_path / 'state.json',
        keys=(FakeKeys(),),
        wifi=FakeWifi(),
        erased=(data,),
    )
    queue: asyncio.Queue[object] = asyncio.Queue()
    work, _ = device_work(
        ports=DevicePorts(resets=resets),
        app=FakeApp(),
        effect=ResetDevice(kind=ResetKind.ERASE),
        queue=queue,
    )
    await work
    assert not data.exists()
    assert queue.get_nowait() == ResetDone(kind=ResetKind.ERASE)


async def test_reset_waits_for_join_in_flight(tmp_path: Path) -> None:
    """A reset waits for a join holding the radio, then forgets it."""
    wifi = FakeWifi(outcome=JoinOutcome.JOINED)
    resets = FileResets(
        data_dir=tmp_path,
        state=tmp_path / 'state.json',
        keys=(FakeKeys(),),
        wifi=wifi,
        erased=(tmp_path / 'saved.db',),
    )
    ports = DevicePorts(resets=resets, wifi=wifi)
    queue: asyncio.Queue[object] = asyncio.Queue()
    async with ports.radio:
        work, _ = device_work(
            ports=ports,
            app=FakeApp(),
            effect=ResetDevice(kind=ResetKind.ERASE),
            queue=queue,
        )
        task = asyncio.ensure_future(work)
        for _ in range(5):
            await asyncio.sleep(0)
        assert wifi.forgotten == 0
        await wifi.join(request=REQUEST)
    await task
    assert wifi.joined == [REQUEST]
    assert wifi.forgotten == 1


async def setup_work(
    *, wifi: FakeWifi, effect: SetupEffect,
) -> tuple[list[object], object]:
    """
    Run a Wi-Fi setup effect on a fake and give what it posted.

    Parameters
    ----------
    wifi : FakeWifi
        The Wi-Fi control.
    effect : SetupEffect
        The effect.

    Returns
    -------
    tuple[list[object], object]
        The events posted, and the event posted if the work crashes.
    """
    queue: asyncio.Queue[object] = asyncio.Queue()
    work, fallback = device_work(
        ports=DevicePorts(wifi=wifi), app=FakeApp(), effect=effect,
        queue=queue,
    )
    await work
    return drained(queue=queue), fallback


async def test_start_setup_posts_networks_and_password() -> None:
    """Start scans, then starts the hotspot with a new password."""
    wifi = FakeWifi(networks=(HOME,), hotspot_ok=True)
    posted, fallback = await setup_work(wifi=wifi, effect=StartWifiSetup())
    assert wifi.calls == ['active', 'scan', 'start']
    assert len(wifi.passwords[0]) == 8
    assert posted == [
        HotspotStarted(networks=(HOME,), password=wifi.passwords[0]),
    ]
    assert fallback == HotspotFailed()
    await setup_work(wifi=wifi, effect=StartWifiSetup())
    assert wifi.passwords[0] != wifi.passwords[1]


async def test_start_setup_failed_hotspot() -> None:
    """A hotspot that won't start posts HotspotFailed."""
    posted, _ = await setup_work(wifi=FakeWifi(), effect=StartWifiSetup())
    assert posted == [HotspotFailed()]


async def test_failed_join_restarts_hotspot_same_password() -> None:
    """A failed join brings the hotspot back with the same password."""
    wifi = FakeWifi(outcome=JoinOutcome.WRONG_PASSWORD, hotspot_ok=True)
    effect = JoinWifi(request=REQUEST, hotspot='7Q4K9W2M')
    posted, fallback = await setup_work(wifi=wifi, effect=effect)
    assert wifi.calls == ['stop', 'join', 'start']
    assert wifi.passwords == ['7Q4K9W2M']
    assert posted == [
        WifiJoinFailed(
            ssid='Home', outcome=JoinOutcome.WRONG_PASSWORD, hotspot=True,
        ),
    ]
    assert fallback == WifiJoinFailed(
        ssid='Home', outcome=JoinOutcome.FAILED, hotspot=False,
    )


async def test_join_success_posts_joined() -> None:
    """A join that works leaves the hotspot down and says so."""
    wifi = FakeWifi(outcome=JoinOutcome.JOINED)
    effect = JoinWifi(request=REQUEST, hotspot='7Q4K9W2M')
    posted, _ = await setup_work(wifi=wifi, effect=effect)
    assert wifi.calls == ['stop', 'join']
    assert wifi.joined == [REQUEST]
    assert posted == [WifiJoined(ssid='Home')]


async def test_stop_setup_brings_home_wifi_back() -> None:
    """Stop takes the hotspot down, then rejoins the network it left."""
    wifi = FakeWifi(active='u-home', networks=(HOME,), hotspot_ok=True)
    ports = DevicePorts(wifi=wifi)
    queue: asyncio.Queue[object] = asyncio.Queue()
    fallback: object = None
    for effect in (StartWifiSetup(), StopWifiSetup()):
        work, fallback = device_work(
            ports=ports, app=FakeApp(), effect=effect, queue=queue,
        )
        await work
    assert wifi.calls == ['active', 'scan', 'start', 'stop', 'up u-home']
    assert queue.get_nowait() == HotspotStarted(
        networks=(HOME,), password=wifi.passwords[0],
    )
    assert queue.get_nowait() == WifiSetupStopped()
    assert fallback == WifiSetupStopped()


async def test_stop_setup_without_home_wifi() -> None:
    """With no network to go back to, stop only takes the hotspot down."""
    wifi = FakeWifi()
    posted, _ = await setup_work(wifi=wifi, effect=StopWifiSetup())
    assert wifi.calls == ['stop']
    assert posted == [WifiSetupStopped()]


async def test_leftover_hotspot_stopped() -> None:
    """Start-up stops a hotspot a crashed run left behind."""
    wifi = FakeWifi(saved=True)
    await leftover_stopped(ports=DevicePorts(wifi=wifi))
    assert wifi.calls == ['saved', 'stop']
    clean = FakeWifi()
    await leftover_stopped(ports=DevicePorts(wifi=clean))
    assert clean.calls == ['saved']
