"""What the device knows, and the timings both its halves share."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Final

from paperroute.core.map_state import MapState
from paperroute.core.map_view import MapView
from paperroute.core.model import Departure, Pin
from paperroute.core.realtime import RealtimeSnapshot
from paperroute.core.saved import LiveView
from paperroute.core.stop_view import StopView

POLL_EVERY: Final[timedelta] = timedelta(seconds=30)
RELOAD_BEFORE: Final[timedelta] = timedelta(minutes=30)
LOOKBACK: Final[timedelta] = timedelta(minutes=60)
RETRY_AFTER: Final[timedelta] = timedelta(seconds=30)
FEED_IDLE: Final[timedelta] = timedelta(minutes=10)


@dataclass(frozen=True)
class State:
    """Everything the app knows."""

    pin: Pin
    now: datetime
    departures: tuple[Departure, ...] = ()
    loaded_until: datetime | None = None
    loading: bool = False
    feeds: tuple[str, ...] = ()
    snapshots: Mapping[str, RealtimeSnapshot] = field(default_factory=dict)
    last_fetch: datetime | None = None
    scroll: int = 0
    last_view: StopView | MapView | None = None
    running: bool = True
    view: LiveView = LiveView.STOP
    map: MapState = field(default_factory=MapState)
