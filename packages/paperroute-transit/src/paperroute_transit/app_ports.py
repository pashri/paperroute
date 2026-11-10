"""The adapters transit's own work runs on."""

from dataclasses import dataclass, field

from paperroute_device.ports import Clock
from paperroute_transit.adapters.leg_cache import LegCache
from paperroute_transit.adapters.rebuild import NullBuilder
from paperroute_transit.adapters.tiles_db import NullTiles
from paperroute_transit.measures import StopMeasures
from paperroute_transit.observers import NullObserver
from paperroute_transit.ports import (
    FeedsClient,
    NetworkRepository,
    Observer,
    TileRepository,
    TimetableBuilder,
    TimetableRepository,
)


@dataclass(frozen=True)
class Ports:
    """
    The adapters transit's own work runs on.

    Attributes
    ----------
    timetable : TimetableRepository
        The timetable.
    feeds : FeedsClient
        The live or replayed feeds.
    clock : Clock
        The current time, as the kit keeps it.
    network : NetworkRepository
        The chooser's and runs' reader on the timetable.
    observer : Observer
        Hears the parsed feeds.
    tiles : TileRepository
        The map tiles.
    measures : StopMeasures
        Measures stops along shapes.
    builder : TimetableBuilder
        Rebuilds the timetable.
    legs : LegCache | None
        Caches connection legs, or None.
    """

    timetable: TimetableRepository
    feeds: FeedsClient
    clock: Clock
    network: NetworkRepository
    observer: Observer = field(default_factory=NullObserver)
    tiles: TileRepository = field(default_factory=NullTiles)
    measures: StopMeasures = field(default_factory=StopMeasures)
    builder: TimetableBuilder = field(default_factory=NullBuilder)
    legs: LegCache | None = None
