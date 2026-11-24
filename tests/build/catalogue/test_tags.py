"""Tests for catalogue: tags."""

import logging
import sqlite3
from collections import Counter
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Final

import pytest
from hypothesis import given
from hypothesis import strategies as st

from paperroute_sydney.city import (
    CENTRES,
    SYDNEY,
    SYDNEY_PROJECTION,
    SydneyMode,
)
from paperroute_transit.build.catalogue import (
    bucket_of,
    bucket_order,
    line_name,
    load_catalogue,
    merged_routes,
    route_order,
)
from paperroute_transit.build.route_tags import (
    TAG_MAX,
    CatalogueRoute,
    agency_towns,
    short_tag,
    tags_of,
)
from paperroute_transit.core.city import PlaceNames
from paperroute_transit.core.geo import Point, to_metres
from tests.build.catalogue.helpers import (
    BY_NUMBER,
    RouteRow,
    chooser_rows,
    tiny_db,
)

REAL_DB: Final[Path] = Path(__file__).parents[3] / 'data' / 'timetable.db'


LOGGER: Final[str] = 'paperroute_transit.build.route_tags'


FAR: Final[Point] = to_metres(
    lon=146.0,
    lat=-34.3,
    projection=SYDNEY_PROJECTION,
)


NAMES: Final[PlaceNames] = SYDNEY.catalogue.places


PLACES: Final[tuple[str, ...]] = (
    'Alpha', 'Bravo', 'Charlie', 'Delta Heights', 'Campbelltown North',
    'Campbelltown South', 'City Martin Place', 'City Wynyard',
)


def centre(*, label: str) -> Point:
    """
    Give the point of one of the CENTRES.

    Parameters
    ----------
    label : str
        Centre label.

    Returns
    -------
    Point
        Metres east and north.
    """
    lon, lat = next(
        (lon, lat) for name, lon, lat, _ in CENTRES if name == label
    )
    return to_metres(lon=lon, lat=lat, projection=SYDNEY_PROJECTION)


def bus(
    *, number: str, long_name: str, middle: Point | None = FAR,
    agency: str = 'A1',
) -> CatalogueRoute:
    """
    Build a merged bus route.

    Parameters
    ----------
    number : str
        Route number.
    long_name : str
        Long name.
    middle : Point | None
        Median station point.
    agency : str
        Operator.

    Returns
    -------
    CatalogueRoute
        Route.
    """
    return CatalogueRoute(
        route_key=f'bus:{number}:{long_name}', kind=SydneyMode.BUS,
        number=number, long_name=long_name, agency=agency, middle=middle,
    )


class Collected(logging.Handler):
    """A log handler that keeps each message."""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.found: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        """
        Keep one record's message.

        Parameters
        ----------
        record : logging.LogRecord
            Record.
        """
        self.found.append(record.getMessage())


@contextmanager
def warnings_logged() -> Iterator[list[str]]:
    """
    Collect the catalogue's warnings.

    Yields
    ------
    list[str]
        Messages, filled as they are logged.
    """
    handler = Collected()
    logger = logging.getLogger(LOGGER)
    logger.addHandler(handler)
    try:
        yield handler.found
    finally:
        logger.removeHandler(handler)


def test_bucket_of() -> None:
    """Numbers fall in hundreds; lettered routes go by their letters."""
    assert bucket_of(number='7') == '1\u201399'
    assert bucket_of(number='606') == '600s'
    assert bucket_of(number='5300') == '5300s'
    assert bucket_of(number='N10') == 'N'
    assert bucket_of(number='BN1') == 'BN'


def test_bucket_order_puts_numbers_before_letters() -> None:
    """Hundreds come in number order, then lettered groups by letter."""
    buckets = ['N', '5300s', 'BN', '100s', 'B', '1\u201399', '600s']
    assert sorted(buckets, key=lambda name: bucket_order(bucket=name)) == [
        '1\u201399', '100s', '600s', '5300s', 'B', 'BN', 'N',
    ]


def test_route_order_is_number_order() -> None:
    """Numbered names sort by number, then the rest in natural order."""
    names = ['N100', '10', '607X', '1', 'N20', '606', 'S1', 'N10', '100']
    assert sorted(names, key=lambda name: route_order(number=name)) == [
        '1', '10', '100', '606', '607X', 'N10', 'N20', 'N100', 'S1',
    ]


def test_line_name() -> None:
    """The code, 'Line' and 'and' are tidied away."""
    assert line_name(
        long_name='T1 North Shore and Western Line', code='T1',
    ) == 'North Shore & Western'
    assert line_name(long_name='Hunter Line', code='HUN') == 'Hunter'


def test_repeated_numbers_get_place_tags() -> None:
    """Two route 10s in different centres are tagged with the centres."""
    south = bus(number='10', long_name='Fairy Meadow to Wollongong',
                middle=centre(label='Wollongong'))
    north = bus(number='10', long_name='Wyong to Tuggerah',
                middle=centre(label='C Coast'))
    assert tags_of(
        routes=[south, north],
        towns={},
        projection=SYDNEY_PROJECTION,
        places=NAMES,
    ) == {
        south.route_key: 'Wollongong', north.route_key: 'C Coast',
    }


def test_clashing_tags_fall_back_to_first_place() -> None:
    """Two 600s with the same operator town take their first places."""
    one = bus(number='600', long_name='Leeton to Griffith')
    two = bus(number='600', long_name='Narrandera to Griffith')
    tags = tags_of(
        routes=[one, two],
        towns={'A1': 'Griffith'},
        projection=SYDNEY_PROJECTION,
        places=NAMES,
    )
    assert tags == {one.route_key: 'Leeton', two.route_key: 'Narrandera'}


def test_unknown_town_is_never_a_tag() -> None:
    """A route with no centre and no operator town takes its first place."""
    known = bus(number='70', long_name='Coledale to Wollongong',
                middle=centre(label='Wollongong'))
    unknown = bus(number='70', long_name='Leeton to Griffith', agency='Z9')
    tags = tags_of(
        routes=[known, unknown],
        towns={},
        projection=SYDNEY_PROJECTION,
        places=NAMES,
    )
    assert tags == {known.route_key: 'Wollongong', unknown.route_key: 'Leeton'}


def test_lone_leftover_takes_its_number_without_a_warning() -> None:
    """One route with no usable value or word is tagged with its number."""
    known = bus(number='70', long_name='Coledale to Wollongong',
                middle=centre(label='Wollongong'))
    blank = bus(number='70', long_name='', agency='Z9')
    with warnings_logged() as found:
        tags = tags_of(
            routes=[known, blank],
            towns={},
            projection=SYDNEY_PROJECTION,
            places=NAMES,
        )
    assert tags == {known.route_key: 'Wollongong', blank.route_key: '70'}
    assert not found


def test_agency_ties_go_to_the_lowest_id(tmp_path: Path) -> None:
    """Operators running equal trips of a route give way to the lowest ID."""
    with closing(tiny_db(path=tmp_path / 't.db', routes=[
        RouteRow(route_id='r1', number='78', long_name='A to B',
                 agency='X2', patterns=(('B', ('S1', 'S2'), 4),)),
        RouteRow(route_id='r2', number='78', long_name='A to B',
                 agency='X1', patterns=(('B', ('S1', 'S3'), 4),)),
    ])) as conn:
        (route,) = merged_routes(
            conn=conn, projection=SYDNEY_PROJECTION, classify=SYDNEY.classify,
            by_number=BY_NUMBER,
        )
    assert route.agency == 'X1'


def test_identical_long_names_merge_into_one_untagged_row(
    tmp_path: Path,
) -> None:
    """Two operators' route 78 of one long name make one untagged row."""
    with closing(tiny_db(path=tmp_path / 't.db', routes=[
        RouteRow(route_id='r1', number='78', long_name='A to B',
                 agency='X1', patterns=(('B', ('S1', 'S2'), 3),)),
        RouteRow(route_id='r2', number='78', long_name='A to B',
                 agency='X2', patterns=(('B', ('S1', 'S3'), 5),)),
    ])) as conn:
        (route,) = merged_routes(
            conn=conn, projection=SYDNEY_PROJECTION, classify=SYDNEY.classify,
            by_number=BY_NUMBER,
        )
        assert route.agency == 'X2'
        assert route.route_key == 'bus:78:A to B'
        assert load_catalogue(
            conn=conn, projection=SYDNEY_PROJECTION, classify=SYDNEY.classify,
            modes=SYDNEY.modes, rules=SYDNEY.catalogue,
        ) == 1
        assert chooser_rows(conn=conn, kind='bus') == [
            ('78', 'A to B', '', ''),
        ]


def test_merged_route_middle_is_the_median_station(tmp_path: Path) -> None:
    """The middle is the median of every merged route's stations."""
    routes = [
        RouteRow(route_id='r1', number='5', long_name='A to B',
                 patterns=(('B', ('S1', 'S2'), 1),)),
        RouteRow(route_id='r2', number='5', long_name='A to B',
                 patterns=(('B', ('S2', 'S3'), 1),)),
    ]
    with closing(tiny_db(path=tmp_path / 't.db', routes=routes)) as conn:
        conn.executemany(
            'UPDATE stops SET lon = ?, lat = ? WHERE stop_id = ?',
            [(150.0, -33.0, 'S1'), (151.0, -34.0, 'S2'),
             (152.5, -35.0, 'S3')],
        )
        (route,) = merged_routes(
            conn=conn, projection=SYDNEY_PROJECTION, classify=SYDNEY.classify,
            by_number=BY_NUMBER,
        )
    assert route.middle == pytest.approx(to_metres(
        lon=151.0,
        lat=-34.0,
        projection=SYDNEY_PROJECTION,
    ))


def test_tags_fall_through_to_last_place() -> None:
    """Routes with one region and first place take their last places."""
    one = bus(number='21', long_name='Lake Haven to Tuggerah',
              middle=centre(label='C Coast'))
    two = bus(number='21', long_name='Lake Haven to Wyong',
              middle=centre(label='C Coast'))
    tags = tags_of(
        routes=[one, two],
        towns={},
        projection=SYDNEY_PROJECTION,
        places=NAMES,
    )
    assert tags == {one.route_key: 'Tuggerah', two.route_key: 'Wyong'}


def test_tags_fall_through_to_via_place() -> None:
    """Routes with one region and both ends take their via places."""
    one = bus(number='30', long_name='A to B via C')
    two = bus(number='30', long_name='A to B via D')
    tags = tags_of(
        routes=[one, two],
        towns={'A1': 'A'},
        projection=SYDNEY_PROJECTION,
        places=NAMES,
    )
    assert tags == {one.route_key: 'C', two.route_key: 'D'}


def test_short_tag_keeps_cut_names_distinct() -> None:
    """Cut names that would read the same keep their ends."""
    north = short_tag(
        place='Campbelltown North', places=NAMES, other='Campbelltown South',
    )
    south = short_tag(
        place='Campbelltown South', places=NAMES, other='Campbelltown North',
    )
    assert (north, south) == ('Camp.North', 'Camp.South')
    assert max(len(north), len(south)) <= TAG_MAX
    assert short_tag(place='City Town Hall', places=NAMES) == 'City Town.'
    assert short_tag(place='Campbelltown', places=NAMES) == 'Campbelltn'


def test_surviving_clash_falls_back_to_first_differing_word() -> None:
    """Routes alike at every level take the word that tells them apart."""
    one = bus(number='40', long_name='A to B via C (Express)')
    two = bus(number='40', long_name='A to B via C (Limited)')
    with warnings_logged() as found:
        tags = tags_of(
            routes=[one, two],
            towns={'A1': 'A'},
            projection=SYDNEY_PROJECTION,
            places=NAMES,
        )
    assert tags == {one.route_key: 'Express', two.route_key: 'Limited'}
    assert len(found) == 1
    assert one.route_key in found[0]
    assert two.route_key in found[0]


def test_indistinguishable_routes_share_a_tag_with_a_warning() -> None:
    """Long names differing only in spacing share a tag and a warning."""
    one = bus(number='50', long_name='A to  B')
    two = bus(number='50', long_name='A to B')
    with warnings_logged() as found:
        tags = tags_of(
            routes=[one, two],
            towns={'A1': 'A'},
            projection=SYDNEY_PROJECTION,
            places=NAMES,
        )
    assert tags[one.route_key] == tags[two.route_key] != ''
    assert len(found) == 1


def test_warning_names_every_route_sharing_a_tag() -> None:
    """A word tag that falls back onto a level's tag names both routes."""
    routes = [
        bus(number='10', long_name=name)
        for name in (
            'Alpha to Alpha', 'Alpha to Bravo', 'Alpha to Charlie',
            'Bravo to Alpha', 'Charlie to Alpha',
        )
    ]
    with warnings_logged() as found:
        tags = tags_of(
            routes=routes,
            towns={'A1': 'Alpha'},
            projection=SYDNEY_PROJECTION,
            places=NAMES,
        )
    shared = [
        key
        for key, tag in tags.items()
        if Counter(tags.values())[tag] > 1
    ]
    assert shared
    assert len(found) == 1
    assert all(key in found[0] for key in shared)


@st.composite
def route_groups(draw: st.DrawFn) -> list[CatalogueRoute]:
    """
    Draw route 10s with distinct long names built from place names.

    Parameters
    ----------
    draw : st.DrawFn
        Hypothesis draw.

    Returns
    -------
    list[CatalogueRoute]
        Routes sharing a number.
    """
    places = st.sampled_from(PLACES)
    names = draw(st.lists(
        st.builds(
            lambda first, last, via: (
                f'{first} to {last}' + (f' via {via}' if via else '')
            ),
            places, places, st.none() | places,
        ),
        min_size=2, max_size=6, unique=True,
    ))
    points = st.none() | st.sampled_from([
        FAR, centre(label='Wollongong'), centre(label='C Coast'),
    ])
    return [
        bus(number='10', long_name=name, middle=draw(points),
            agency=draw(st.sampled_from(['A1', 'A2', 'Unknown'])))
        for name in names
    ]


@given(routes=route_groups())
def test_tags_always_distinct(routes: list[CatalogueRoute]) -> None:
    """Tags are short and distinct, and any shared tag is warned about."""
    towns = agency_towns(
        routes=[route for route in routes if route.agency != 'Unknown'],
        places=NAMES,
    )
    with warnings_logged() as found:
        tags = tags_of(
            routes=routes,
            towns=towns,
            projection=SYDNEY_PROJECTION,
            places=NAMES,
        )
    assert set(tags) == {route.route_key for route in routes}
    for tag in tags.values():
        assert 0 < len(tag) <= TAG_MAX
        assert not tag[-1].isdigit()
    shared = [key for key, tag in tags.items()
              if Counter(tags.values())[tag] > 1]
    assert all(any(key in line for line in found) for key in shared)
    assert len(found) <= 1


@pytest.mark.skipif(not REAL_DB.exists(), reason='no real timetable')
def test_real_catalogue_tags_are_distinct() -> None:
    """Every repeated Bus and School number has distinct, non-empty tags."""
    with closing(sqlite3.connect(REAL_DB)) as conn:
        if not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'chooser_routes'",
        ).fetchone():
            pytest.skip('real timetable has no catalogue yet')
        rows = conn.execute(
            "SELECT kind, number, tag FROM chooser_routes "
            "WHERE kind IN ('bus', 'school')",
        ).fetchall()
    groups: dict[tuple[str, str], list[str]] = {}
    for kind, number, tag in rows:
        groups.setdefault((kind, number), []).append(tag)
    repeated = [tags for tags in groups.values() if len(tags) > 1]
    assert repeated
    for tags in repeated:
        assert all(tags), tags
        assert len(set(tags)) == len(tags), tags
