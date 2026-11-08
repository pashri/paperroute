"""The adapters the app runs on."""

from dataclasses import dataclass, field

from paperroute.app_device import DevicePorts
from paperroute_device.adapters.power import NullPower
from paperroute_device.ports import Clock, PowerControl
from paperroute_hardware.ports import Display
from paperroute_transit.adapters.leg_cache import LegCache
from paperroute_transit.adapters.rebuild import NullBuilder
from paperroute_transit.adapters.tiles_db import NullTiles
from paperroute_transit.measures import StopMeasures
from paperroute_transit.observers import NullObserver
from paperroute_transit.ports import (
    FeedsClient,
    NetworkRepository,
    Observer,
    StateRepository,
    TileRepository,
    TimetableBuilder,
    TimetableRepository,
)


@dataclass(frozen=True)
class Ports:
    """The adapters the app runs on."""

    timetable: TimetableRepository
    feeds: FeedsClient
    display: Display
    clock: Clock
    network: NetworkRepository
    states: StateRepository
    observer: Observer = field(default_factory=NullObserver)
    tiles: TileRepository = field(default_factory=NullTiles)
    measures: StopMeasures = field(default_factory=StopMeasures)
    builder: TimetableBuilder = field(default_factory=NullBuilder)
    power: PowerControl = field(default_factory=NullPower)
    device: DevicePorts = field(default_factory=DevicePorts)
    legs: LegCache | None = None
