"""The device's own pages over fixed facts, a fake key store and files."""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta, timezone, tzinfo
from pathlib import Path
from typing import Final

from paperroute_device.adapters.web_device import DevicePages
from paperroute_device.adapters.web_frame import KIT_NAV, NavItem
from paperroute_device.core.device_events import DeviceRequest
from paperroute_device.core.device_model import (
    Credential,
    DeviceFacts,
    DeviceKey,
    DeviceStatus,
    WifiNetwork,
    WifiPhase,
    WifiSetup,
    WifiStatus,
)
from paperroute_hardware.ports import PanelTimes
from paperroute_testkit.device_fakes import FakeKeys

KEY: Final[str] = 'already-set-key-123'
NOW: Final[float] = 1_761_043_500.0  # 21 Oct 2025 21:45 at UTC+11
ZONE: Final[tzinfo] = timezone(timedelta(hours=11))
TEST_KEY: Final[Credential] = Credential(
    label='Test key',
    refusal='Bad test key.',
    not_saved='Test key not saved.',
)
FACTS: Final[DeviceFacts] = DeviceFacts(
    wifi=WifiStatus(managed=True, ssid='Home', dbm=-52, ip='192.168.1.40'),
    clock_synced=True,
    uptime_s=2 * 3600 + 14 * 60 + 5,
    cpu_c=48.2,
    disk_free=21_400_000_000,
    mem_free=134_000_000,
    version='abc1234',
    last_error=None,
    tiles_built='2025-10-19T08:00:00+11:00',
    key_set=True,
)
STATUS: Final[DeviceStatus] = DeviceStatus(
    timetable='2025-10-21T21:45:00+11:00',
    schema='6',
    rebuilding=False,
    rebuild_failed=False,
    calls_today=8412,
    key_set=True,
    facts=FACTS,
)
NETWORKS: Final[tuple[WifiNetwork, ...]] = (
    WifiNetwork(ssid='Home', signal=80, secured=True, enterprise=False),
    WifiNetwork(
        ssid='Cafe:Wi-Fi', signal=40, secured=False, enterprise=False,
    ),
    WifiNetwork(ssid='Corp', signal=20, secured=True, enterprise=True),
)
HOTSPOT: Final[DeviceStatus] = DeviceStatus(
    timetable=None,
    schema=None,
    rebuilding=False,
    rebuild_failed=False,
    calls_today=0,
    key_set=True,
    facts=None,
    setup=WifiSetup(
        phase=WifiPhase.HOTSPOT, password='7Q4K9W2M', networks=NETWORKS,
    ),
)


@dataclass
# pylint: disable-next=too-many-instance-attributes
class DeviceSite:
    """
    What the device pages read, and what they posted.

    Attributes
    ----------
    status : DeviceStatus | None
        The device status the pages read.
    feeds : dict[str, float]
        When each feed last worked.
    panel : PanelTimes | None
        The panel's refresh times, or None.
    knobs : bool | None
        Whether the knobs answer, or None.
    keys : FakeKeys
        The first key's store.
    credential : Credential
        How the pages name the first key.
    key_name : str
        The first key's name.
    more_keys : tuple[DeviceKey, ...]
        The keys after the first.
    feed_label : Callable[[str], str]
        Names a feed.
    daily_limit : int
        The API calls allowed each day.
    zone : tzinfo
        The wall clock the pages show.
    posted : list[DeviceRequest]
        Requests posted so far.
    error_log : Path | None
        The error log.
    state_file : Path | None
        The saved state.
    nav : tuple[NavItem, ...]
        The links in each page's nav.
    """

    status: DeviceStatus | None = STATUS
    feeds: dict[str, float] = field(
        default_factory=lambda: {'trains': NOW - 12, 'buses': NOW - 40},
    )
    panel: PanelTimes | None = None
    knobs: bool | None = None
    keys: FakeKeys = field(default_factory=lambda: FakeKeys(key=KEY))
    credential: Credential = TEST_KEY
    key_name: str = 'test'
    more_keys: tuple[DeviceKey, ...] = ()
    feed_label: Callable[[str], str] = str.title
    daily_limit: int = 60_000
    zone: tzinfo = ZONE
    posted: list[DeviceRequest] = field(default_factory=list)
    error_log: Path | None = None
    state_file: Path | None = None
    nav: tuple[NavItem, ...] = KIT_NAV

    def pages(self) -> DevicePages:
        """
        Build the pages over this site.

        Returns
        -------
        DevicePages
            The pages, at `NOW`.
        """
        return DevicePages(
            status=lambda: self.status,
            feeds=lambda: self.feeds,
            panel=lambda: self.panel,
            knobs=lambda: self.knobs,
            keys=(
                DeviceKey(
                    name=self.key_name,
                    credential=self.credential,
                    store=self.keys,
                ),
                *self.more_keys,
            ),
            feed_label=self.feed_label,
            daily_limit=self.daily_limit,
            post=self.posted.append,
            zone=self.zone,
            error_log=self.error_log,
            state_file=self.state_file,
            clock=lambda: NOW,
            nav=self.nav,
        )
