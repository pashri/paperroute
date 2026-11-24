"""Shared helpers for the app effects tests."""

import asyncio
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime

from paperroute_device.core.kit_effects import Render
from paperroute_device.runtime.outputs import Outputs, carry_out, draw_pending
from paperroute_device.runtime.ports import KitPorts
from paperroute_device_testkit.device_fakes import FixedClock
from paperroute_device_testkit.hardware_fakes import FrameRecorder
from paperroute_sydney.city import SYDNEY, SydneyMode
from paperroute_testkit.core.kit_steps import QUIET_PARTS
from paperroute_testkit.fakes import EmptyPlacedTimetable, MemoryStates
from paperroute_transit.app_ports import Ports
from paperroute_transit.core.alert_model import AlertsSnapshot
from paperroute_transit.core.model import Pin, StopPlace
from paperroute_transit.core.positions import PositionsSnapshot
from paperroute_transit.core.reach import ReachInput, RunTail, StripKey
from paperroute_transit.core.realtime import RealtimeSnapshot
from paperroute_transit.transit_app import TransitApp
from tests.app_factories import AT


def kit_ports(
    *,
    display: FrameRecorder | None = None,
    states: MemoryStates | None = None,
) -> KitPorts:
    """
    Make the kit's ports on fakes, at the shared test time.

    Parameters
    ----------
    display : FrameRecorder | None
        Display; a new recorder by default.
    states : MemoryStates | None
        Saved state store; an empty one by default.

    Returns
    -------
    KitPorts
        The ports.
    """
    return KitPorts(
        display=display or FrameRecorder(),
        clock=FixedClock(at=AT),
        states=states or MemoryStates(),
    )


async def rendered(
    *, app: TransitApp, ports: KitPorts, outputs: Outputs, render: Render,
) -> None:
    """
    Carry out a Render through the kit's outputs, then draw it.

    Parameters
    ----------
    app : TransitApp
        The board, which draws its own views.
    ports : KitPorts
        The kit's adapters.
    outputs : Outputs
        What the kit's effects draw and write through.
    render : Render
        The render.
    """
    await carry_out(
        effects=(render,),
        ports=ports,
        app=app,
        queue=asyncio.Queue(),
        tasks=set(),
        outputs=outputs,
    )
    await draw_pending(ports=ports, app=app, outputs=outputs)


def sydney_app(*, ports: Ports, seed: Pin | None = None) -> TransitApp:
    """
    Make Sydney's board attached to transit's ports.

    Parameters
    ----------
    ports : Ports
        Transit's adapters.
    seed : Pin | None
        Pin to start with when nothing is saved.

    Returns
    -------
    TransitApp
        The attached app.
    """
    app = TransitApp(city=SYDNEY, parts=QUIET_PARTS)
    app.attach(ports=ports, seed=seed)
    return app


@dataclass
class VersionedTimetable(EmptyPlacedTimetable):
    """A timetable whose build can be swapped."""

    built: str | None = 'v1'

    def version(self) -> str | None:
        """
        Name the current build.

        Returns
        -------
        str | None
            The build.
        """
        return self.built


@dataclass
class VersionFailingTimetable(EmptyPlacedTimetable):
    """A timetable whose version read fails."""

    def version(self) -> str | None:
        """
        Fail like a damaged database file.

        Returns
        -------
        str | None
            Never returns.

        Raises
        ------
        sqlite3.Error
            Always.
        """
        raise sqlite3.OperationalError('disk I/O error')


def reach_data() -> ReachInput:
    """
    Make the runs C E1 E2 through the From C.

    Returns
    -------
    ReachInput
        The runs.
    """
    places = {
        key: StopPlace(stop_id=key, name=f'{key} Station', point=None)
        for key in ('C', 'E1', 'E2')
    }
    return ReachInput(
        origin=places['C'],
        tails=(RunTail(before=(), after=('E1', 'E2'), trips=8),),
        places=places,
        station_of={key: key for key in places},
    )


STRIP_KEY = StripKey(station='C', kind=SydneyMode.TRAIN, routes=())


@dataclass
class RecordingParsers:
    """Readers that note each body they are given and find nothing."""

    read: list[tuple[str, bytes]] = field(default_factory=list)

    def trips(self, *, data: bytes, fetched_at: datetime) -> RealtimeSnapshot:
        """
        Note a trip-updates body.

        Parameters
        ----------
        data : bytes
            Feed body.
        fetched_at : datetime
            When it was downloaded.

        Returns
        -------
        RealtimeSnapshot
            No trips.
        """
        self.read.append(('trips', data))
        return RealtimeSnapshot(fetched_at=fetched_at, trips={})

    def positions(
        self, *, data: bytes, fetched_at: datetime,
    ) -> PositionsSnapshot:
        """
        Note a positions body.

        Parameters
        ----------
        data : bytes
            Feed body.
        fetched_at : datetime
            When it was downloaded.

        Returns
        -------
        PositionsSnapshot
            No vehicles.
        """
        self.read.append(('positions', data))
        return PositionsSnapshot(
            fetched_at=fetched_at, header_time=0, vehicles=(),
        )

    def alerts(
        self, *, data: bytes, feed: str, fetched_at: datetime,
    ) -> AlertsSnapshot:
        """
        Note an alerts body.

        Parameters
        ----------
        data : bytes
            Feed body.
        feed : str
            Feed path.
        fetched_at : datetime
            When it was downloaded.

        Returns
        -------
        AlertsSnapshot
            No alerts.
        """
        self.read.append(('alerts', data))
        return AlertsSnapshot(feed=feed, fetched_at=fetched_at, alerts=())
