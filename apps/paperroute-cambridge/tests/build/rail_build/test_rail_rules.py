"""Tests for rail build: rail rules."""

from contextlib import closing
from datetime import date
from pathlib import Path
from typing import Final

from paperroute_cambridge.build.bods_sources import CAMBRIDGE_SOURCES
from paperroute_cambridge.build.rail_cut import KEPT_CRS
from paperroute_cambridge.city import CAMBRIDGE, RAIL_SOURCE, CambridgeFeed
from paperroute_transit.adapters.network_db import SqliteNetworkRepository
from paperroute_transit.adapters.pin_options import SqlitePinOptions
from paperroute_transit.adapters.timetable_db import SqliteTimetableRepository
from paperroute_transit.core.model import StopPlace
from tests.build.rail_build.helpers import (
    BODS,
    RAIL,
    STATION,
    Shelf,
    built,
    departures,
    night,
    rail_date,
    trip_counts,
)
from tests.build.test_rail_cut import changed, with_east_midlands
from tests.conftest import RECORDED_DAY

ELY: Final[StopPlace] = StopPlace(stop_id='rail:ELY', name='Ely', point=None)


def test_each_source_declares_a_size_limit() -> None:
    """The bus zip may reach 250 MB and the national rail zip 150 MB."""
    assert [spec.size_limit for spec in CAMBRIDGE_SOURCES] == [
        250_000_000, 150_000_000,
    ]


def no_coordinates(tables: dict[str, list[dict[str, str]]]) -> None:
    """
    Give every stop 'nan' for its latitude and longitude.

    Parameters
    ----------
    tables : dict[str, list[dict[str, str]]]
        Rows by member, changed in place.
    """
    for row in tables['stops.txt']:
        row['stop_lat'] = row['stop_lon'] = 'nan'


def test_a_rail_file_without_coordinates_keeps_the_earlier_trains(
    data_dir: Path, tmp_path: Path,
) -> None:
    """Trains whose stations have no place are refused, not lost."""
    shelf = Shelf(files={'bods': BODS, 'rail': RAIL})
    night(data_dir=data_dir, shelf=shelf)
    shelf.files['rail'] = changed(
        out=tmp_path / 'bad.zip', change=no_coordinates,
    )
    report = night(data_dir=data_dir, shelf=shelf)
    assert (report.failed, report.all_failed) == (('rail',), False)
    assert trip_counts(data_dir=data_dir) == {'bods': 67, 'rail': 5}
    assert rail_date(data_dir=data_dir) == date(2026, 11, 16)


def test_an_east_midlands_train_leaves_ely(tmp_path: Path) -> None:
    """A train calling at Ely and not Cambridge is built, badged EM."""
    rail = changed(out=tmp_path / 'em.zip', change=with_east_midlands)
    path = built(
        out=tmp_path / 'timetable.db', start=RECORDED_DAY, days=1, rail=rail,
    )
    assert ('07:45', 'EM', '', 'Peterborough', 'train', 'train') in (
        departures(path=path, day=RECORDED_DAY, place=ELY)
    )


def test_rail_cut_rule_is_the_sorted_kept_stations() -> None:
    """The rail source's rule is derived from the kept stations."""
    rail = next(s for s in CAMBRIDGE_SOURCES if s.name == RAIL_SOURCE)
    assert rail.cut_rule == ','.join(sorted(KEPT_CRS))


def test_trains_reach_every_later_station(rail_built: Path) -> None:
    """Only train runs count when the kind is train."""
    with closing(
        SqliteNetworkRepository(city=CAMBRIDGE, path=rail_built),
    ) as network:
        options = SqlitePinOptions(network=network)
        trains = options.reached_from(place=STATION, kind='train')
        buses = options.reached_from(place=STATION, kind='bus')
    assert [place.name for place in trains] == [
        'Birmingham New Street', 'Brighton', 'Cambridge North',
        'Cambridge South', 'Ely', 'London Kings Cross',
        'London Liverpool Street', 'London St Pancras International',
        'Royston',
    ]
    assert not buses


def test_route_trips_sort_rail_and_bods_by_feed_source(
    rail_built: Path,
) -> None:
    """A rail route is a train and a bods route a bus or coach."""
    with closing(
        SqliteTimetableRepository(path=rail_built, city=CAMBRIDGE),
    ) as repo:
        found = {
            (route, source): len(repo.route_trips(
                route=route, source=source, day=RECORDED_DAY,
            ))
            for route in ('GA', 'A', '777')
            for source in CambridgeFeed
        }
    assert {key for key, count in found.items() if count} == {
        ('GA', CambridgeFeed.TRAIN),
        ('A', CambridgeFeed.BUS),
        ('777', CambridgeFeed.COACH),
    }
