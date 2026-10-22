"""The adapters the app runs on."""

from dataclasses import dataclass, field

from paperroute.adapters.power import NullPower
from paperroute.adapters.rebuild import NullBuilder
from paperroute.adapters.tiles_db import NullTiles
from paperroute.app_device import DevicePorts
from paperroute.measures import StopMeasures
from paperroute.observers import NullObserver
from paperroute.ports import (
    Clock,
    Display,
    FeedsClient,
    NetworkRepository,
    Observer,
    PowerControl,
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
