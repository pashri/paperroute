"""Tests for carrying out the device's effects."""

import asyncio
from dataclasses import replace

import pytest

from paperroute import app_device
from paperroute.app_device import (
    DevicePorts,
    Reported,
    device_work,
    report_status,
)
from paperroute.core.device_model import DeviceStatus
from paperroute.core.messages import (
    DeviceProbed,
    DeviceProbeFailed,
    Event,
    ProbeDevice,
)
from paperroute.core.state import step
from paperroute.observers import Timings
from tests.core.test_state import fresh, loaded
from tests.fakes import FakeProbe, HangingProbe, device_facts


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
        ports=ports, effect=ProbeDevice(), queue=queue,
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
