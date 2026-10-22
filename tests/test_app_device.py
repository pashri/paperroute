"""Tests for carrying out the device's effects."""

import asyncio

from paperroute.app_device import DevicePorts, device_work
from paperroute.core.messages import (
    DeviceProbed,
    DeviceProbeFailed,
    Event,
    ProbeDevice,
)
from tests.fakes import FakeProbe, device_facts


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
