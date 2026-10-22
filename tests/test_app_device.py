"""Tests for carrying out the device's effects."""

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest

from paperroute import app_device
from paperroute.adapters.resets import FileResets
from paperroute.app_device import (
    DevicePorts,
    Reported,
    device_work,
    report_status,
)
from paperroute.core.device_model import (
    DeviceStatus,
    JoinOutcome,
    JoinRequest,
    ResetKind,
    WifiNetwork,
)
from paperroute.core.messages import (
    DeviceProbed,
    DeviceProbeFailed,
    Event,
    HotspotFailed,
    HotspotStarted,
    JoinWifi,
    ProbeDevice,
    ResetDevice,
    ResetDone,
    StartWifiSetup,
    StopWifiSetup,
    WifiJoined,
    WifiJoinFailed,
    WifiSetupStopped,
)
from paperroute.core.state import step
from paperroute.observers import Timings
from tests.core.test_state import fresh, loaded
from tests.fakes import (
    FakeBuilder,
    FakeKeys,
    FakeProbe,
    FakeWifi,
    HangingProbe,
    RecordingResets,
    WatchingBuilder,
    device_facts,
)


async def worked(*, ports: DevicePorts) -> tuple[list[Event], Event]:
    """
    Run a probe effect and give what it posted and its fallback.

    Parameters
    ----------
    ports : DevicePorts
        Device adapters.

    Returns
    -------
    tuple[list[Event], Event]
        The events posted, and the event posted if the work crashes.
    """
    queue: asyncio.Queue[Event] = asyncio.Queue()
    work, fallback = device_work(
        ports=ports, effect=ProbeDevice(), queue=queue, builder=FakeBuilder(),
    )
    await work
    posted: list[Event] = []
    while not queue.empty():
        posted.append(queue.get_nowait())
    return posted, fallback


async def test_probe_effect_posts_facts() -> None:
    """A probe posts the facts it found."""
    posted, fallback = await worked(ports=DevicePorts(probe=FakeProbe()))
    assert posted == [DeviceProbed(facts=device_facts())]
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
    monkeypatch.setattr(app_device, 'PROBE_TIMEOUT', 0.01)
    posted, _ = await worked(ports=DevicePorts(probe=HangingProbe()))
    assert posted == [DeviceProbeFailed()]


def test_status_ignores_changing_readings() -> None:
    """Probes differing only in uptime, CPU, memory and disk report once."""
    timings = Timings()
    reported = Reported()
    state = loaded(state=fresh())
    first = device_facts()
    second = replace(
        first, uptime_s=99.0, cpu_c=50.5, mem_free=1, disk_free=2,
    )
    seen: list[DeviceStatus | None] = []
    for facts in (first, second):
        state, _ = step(state, DeviceProbed(facts=facts))
        report_status(observer=timings, state=state, reported=reported)
        seen.append(timings.device)
        timings.device = None
    assert seen[0] is not None
    assert seen[1] is None


def test_unchanged_probe_does_not_redraw() -> None:
    """A probe giving the same facts again draws nothing."""
    state, _ = step(loaded(state=fresh()), DeviceProbed(facts=device_facts()))
    _, effects = step(state, DeviceProbed(facts=device_facts()))
    assert not effects


async def test_reset_work_posts_done() -> None:
    """A reset runs the resets, then posts that it is done."""
    resets = RecordingResets()
    queue: asyncio.Queue[Event] = asyncio.Queue()
    work, fallback = device_work(
        ports=DevicePorts(resets=resets),
        effect=ResetDevice(kind=ResetKind.ERASE),
        queue=queue,
        builder=FakeBuilder(),
    )
    await work
    assert resets.kinds == [ResetKind.ERASE]
    assert queue.get_nowait() == ResetDone(kind=ResetKind.ERASE)
    assert fallback == ResetDone(kind=ResetKind.ERASE)


async def test_reset_stops_rebuild_before_deleting(tmp_path: Path) -> None:
    """A running rebuild is stopped before the timetable is deleted."""
    timetable = tmp_path / 'timetable.db'
    timetable.write_text('x', encoding='utf-8')
    builder = WatchingBuilder(watched=timetable)
    resets = FileResets(
        data_dir=tmp_path,
        state=tmp_path / 'state.json',
        key=FakeKeys(),
        wifi=FakeWifi(),
        timetable=timetable,
    )
    queue: asyncio.Queue[Event] = asyncio.Queue()
    work, _ = device_work(
        ports=DevicePorts(resets=resets),
        effect=ResetDevice(kind=ResetKind.ERASE),
        queue=queue,
        builder=builder,
    )
    await work
    assert builder.existed == [True]
    assert not timetable.exists()


HOME = WifiNetwork(ssid='Home', signal=80, secured=True, enterprise=False)
REQUEST = JoinRequest(ssid='Home', password='hunter22')


async def setup_work(
    *, wifi: FakeWifi, effect: StartWifiSetup | JoinWifi | StopWifiSetup
) -> tuple[list[Event], Event]:
    """
    Run a Wi-Fi setup effect on a fake and give what it posted.

    Parameters
    ----------
    wifi : FakeWifi
        The Wi-Fi control.
    effect : StartWifiSetup | JoinWifi | StopWifiSetup
        The effect.

    Returns
    -------
    tuple[list[Event], Event]
        The events posted, and the event posted if the work crashes.
    """
    queue: asyncio.Queue[Event] = asyncio.Queue()
    work, fallback = device_work(
        ports=DevicePorts(wifi=wifi),
        effect=effect,
        queue=queue,
        builder=FakeBuilder(),
    )
    await work
    posted: list[Event] = []
    while not queue.empty():
        posted.append(queue.get_nowait())
    return posted, fallback


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
    queue: asyncio.Queue[Event] = asyncio.Queue()
    for effect in (StartWifiSetup(), StopWifiSetup()):
        work, fallback = device_work(
            ports=ports, effect=effect, queue=queue, builder=FakeBuilder(),
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
    await app_device.leftover_stopped(ports=DevicePorts(wifi=wifi))
    assert wifi.calls == ['saved', 'stop']
    clean = FakeWifi()
    await app_device.leftover_stopped(ports=DevicePorts(wifi=clean))
    assert clean.calls == ['saved']
