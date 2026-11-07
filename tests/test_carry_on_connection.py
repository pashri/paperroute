"""Tests for a connection whose leg 2 stays on as the next trip."""

import asyncio
from collections.abc import Iterator, Mapping
from contextlib import closing
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path
from typing import Final

import pytest

from paperroute.adapters.network_db import SqliteNetworkRepository
from paperroute.adapters.timetable_db import SqliteTimetableRepository
from paperroute.app_connections import departures_read
from paperroute.app_ports import Ports
from paperroute.build.timetable import Window, build_timetable
from paperroute.core.chains import chains_of
from paperroute.core.connection_strip import ConnectionStripView
from paperroute.core.map_view import DEFAULT_ZOOM
from paperroute.core.messages import (
    Effect,
    Event,
    LoadDepartures,
    Render,
    ScreenView,
    Tick,
)
from paperroute.core.saved import LiveView, Saved
from paperroute.core.state import initial_state, step
from paperroute.core.state_model import State
from paperroute.ports import TimetableRepository
from paperroute_testkit.app_factories import ports_of, posted_by
from paperroute_testkit.core.factories import live
from paperroute_testkit.fakes import FixedClock
from paperroute_transit.build.gtfs_files import Source
from paperroute_transit.core.model import SYDNEY, Change, Pin, RouteKind, Way

REAL: Final[Path] = Path(__file__).parent / 'fixtures' / 'real'
DAY: Final[date] = date(2026, 10, 16)
M9B_DB: Final[Path] = Path(__file__).parents[1] / 'data' / 'timetable-m9b.db'
M9B_AT: Final[datetime] = datetime(2026, 10, 28, 8, 0, tzinfo=SYDNEY)


@pytest.fixture(name='built', scope='module')
def _built(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """
    Build the 606 and a T1 that ends at Central and runs on as the next.

    Parameters
    ----------
    tmp_path_factory : pytest.TempPathFactory
        Temporary directories.

    Returns
    -------
    Path
        The timetable file.
    """
    out = tmp_path_factory.mktemp('carry') / 'timetable.db'
    build_timetable(
        sources=[
            Source(name='sydneytrains', archive=REAL / 'carry_on.zip'),
            Source(name='bundle', archive=REAL / 'bundle.zip'),
        ],
        window=Window(start=DAY, days=1),
        out=out,
    )
    return out


@pytest.fixture(name='timetable', scope='module')
def _timetable(built: Path) -> Iterator[SqliteTimetableRepository]:
    """
    Open the built timetable.

    Parameters
    ----------
    built : Path
        The timetable file.

    Yields
    ------
    SqliteTimetableRepository
        The timetable.
    """
    with closing(SqliteTimetableRepository(path=built)) as timetable:
        yield timetable


CARRY_PIN: Final[dict[str, str]] = {
    'origin': '2145615', 'alight': '2150119', 'board': '215020',
    'destination': '200070',
}
M9B_CARRY: Final[dict[str, str]] = {
    'origin': 'G215254', 'alight': '215020', 'board': '215020',
    'destination': '200070',
}


def carry_pin(
    *, timetable: TimetableRepository, ids: Mapping[str, str] = CARRY_PIN,
) -> Pin:
    """
    The 606 to Parramatta, then a T1 on to Town Hall.

    Parameters
    ----------
    timetable : TimetableRepository
        Timetable the places come from.
    ids : Mapping[str, str]
        Stop IDs of the origin, alight, board and destination.

    Returns
    -------
    Pin
        The connection pin.
    """
    stop = timetable.stop
    return Pin(
        origin=stop(stop_id=ids['origin']),
        destination=stop(stop_id=ids['destination']),
        kind=RouteKind.BUS,
        ways=(
            Way(
                changes=(
                    Change(
                        alight=stop(stop_id=ids['alight']),
                        board=stop(stop_id=ids['board']),
                        allow=5,
                    ),
                ),
            ),
        ),
    )


async def settled_view(*, ports: Ports, state: State) -> ScreenView | None:
    """
    Run the app's steps and effects until no more events come.

    Parameters
    ----------
    ports : Ports
        Adapters.
    state : State
        Starting state.

    Returns
    -------
    ScreenView | None
        The last view rendered.
    """
    events: list[Event] = [Tick(now=ports.clock.now())]
    view = None
    while events:
        effects: list[Effect] = []
        for event in events:
            state, more = step(state, event)
            effects.extend(more)
        for effect in effects:
            if isinstance(effect, Render):
                view = effect.view
        events = await posted_by(
            ports=ports,
            effects=[
                effect
                for effect in effects
                if not isinstance(effect, Render)
            ],
        )
    return view


def connection_strip(
    *, path: Path, pins: tuple[Pin, ...], current: int, now: datetime,
) -> ConnectionStripView:
    """
    Show a connection pin's Strip as the app does, from a timetable file.

    Parameters
    ----------
    path : Path
        Timetable file.
    pins : tuple[Pin, ...]
        Pins.
    current : int
        Index of the pin shown.
    now : datetime
        Current time.

    Returns
    -------
    ConnectionStripView
        The Strip once every read has come back.
    """
    with (
        closing(SqliteTimetableRepository(path=path)) as timetable,
        closing(SqliteNetworkRepository(path=path)) as network,
    ):
        ports = replace(
            ports_of(timetable=timetable),
            network=network,
            clock=FixedClock(at=now),
        )
        state = initial_state(
            saved=Saved(
                pins=pins,
                current=current,
                unpinned=None,
                view=LiveView.STRIP,
            ),
            now=now,
            stored=True,
        )
        view = asyncio.run(settled_view(ports=ports, state=state))
    assert isinstance(view, ConnectionStripView)
    return view


def at(hour: int, minute: int) -> datetime:
    """
    A Sydney time on the fixture's day.

    Parameters
    ----------
    hour : int
        Hour.
    minute : int
        Minute.

    Returns
    -------
    datetime
        Aware Sydney time.
    """
    return datetime(
        DAY.year, DAY.month, DAY.day, hour, minute, tzinfo=SYDNEY,
    )


def test_leg_two_follows_carry_on_to_town_hall(
    timetable: SqliteTimetableRepository,
) -> None:
    """606 to Parramatta, then the T1 that becomes a city trip at Central."""
    stop = timetable.stop
    pin = Pin(
        origin=stop(stop_id='2145615'),
        destination=stop(stop_id='200070'),
        kind=RouteKind.BUS,
        ways=(
            Way(
                changes=(
                    Change(
                        alight=stop(stop_id='2150119'),
                        board=stop(stop_id='215020'),
                        allow=5,
                    ),
                ),
            ),
        ),
    )
    loaded = departures_read(
        timetable=timetable,
        effect=LoadDepartures(pin=pin, start=at(6, 0), end=at(8, 0)),
    )
    assert loaded.legs is not None
    chains = chains_of(
        pin=pin,
        lists={
            key: sorted(
                (live(item=item) for item in items),
                key=lambda item: item.departs,
            )
            for key, items in loaded.legs.lists.items()
        },
        direct=[live(item=item) for item in loaded.departures],
        now=at(6, 0),
    )
    trains = [
        chain.legs[-1].departure
        for chain in chains
        if chain.legs[-1].departure.route == 'T1'
    ]
    assert [train.trip_id for train in trains] == [
        '169C.947.173.64.A.8.91436348',
    ]
    assert trains[0].departs.strftime('%H:%M') == '07:01'


def test_connection_strip_follows_carry_on_to_town_hall(
    built: Path, timetable: SqliteTimetableRepository,
) -> None:
    """The T1 leg's rows run on through Central to Town Hall."""
    view = connection_strip(
        path=built,
        pins=(carry_pin(timetable=timetable),),
        current=0,
        now=at(6, 0),
    )
    train = view.sections[-1]
    assert train.empty is None
    assert train.stops[-1].name == 'Town Hall Station'


@pytest.mark.skipif(
    not M9B_DB.exists(), reason='needs data/timetable-m9b.db',
)
def test_m9b_connection_strips_load_their_lines() -> None:
    """Both carry-on connections show every leg's stops."""
    with closing(SqliteTimetableRepository(path=M9B_DB)) as timetable:
        stop = timetable.stop
        pins = (
            carry_pin(timetable=timetable, ids=M9B_CARRY),
            Pin(
                origin=stop(stop_id='G257060'),
                destination=stop(stop_id='200070'),
                kind=RouteKind.BUS,
                ways=(
                    Way(
                        changes=(
                            Change(
                                alight=stop(stop_id='256020'),
                                board=stop(stop_id='256020'),
                                allow=5,
                            ),
                        ),
                    ),
                ),
            ),
        )
    for current in range(len(pins)):
        view = connection_strip(
            path=M9B_DB, pins=pins, current=current, now=M9B_AT,
        )
        assert [section.empty for section in view.sections] == [None, None]
