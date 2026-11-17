"""Tests for the Cambridge city definition."""

import logging
from datetime import time
from zoneinfo import ZoneInfo

import pytest

from paperroute_cambridge.build.bods_sources import CAMBRIDGE_SOURCES
from paperroute_cambridge.city import (
    CAMBRIDGE,
    DRUMMER_STREET,
    classify_cambridge,
    merge_cambridge,
)
from paperroute_device.core.device_settings import Window
from paperroute_transit.core.city import MergeReport
from paperroute_transit.core.model import Pin
from paperroute_transit.core.modes import RouteClass, mode_problems
from paperroute_transit.core.positions import ParkedAge
from tests.build.timetable_rows import (
    MILDENHALL,
    NORWICH,
    PARKSIDE,
    STANSTED,
    calls_of,
    loaded,
    parents,
    route_ids,
)


def test_city_has_the_spec_values() -> None:
    """City has the spec values."""
    assert CAMBRIDGE.zone == ZoneInfo('Europe/London')
    assert CAMBRIDGE.peak_windows == (
        Window(start=time(7), end=time(9, 30)),
        Window(start=time(16), end=time(18, 30)),
    )
    assert CAMBRIDGE.parked_age is ParkedAge.TEN
    assert CAMBRIDGE.route_fallback is True
    assert [(m.id, m.letter, m.chip) for m in CAMBRIDGE.modes] == [
        ('bus', 'B', '#d9d8d0'),
        ('coach', 'C', '#e03c31'),
    ]
    assert mode_problems(modes=CAMBRIDGE.modes) == []
    assert CAMBRIDGE.default_pin == Pin(origin=DRUMMER_STREET, kind='bus')


@pytest.mark.parametrize(
    ('route_type', 'agency', 'short', 'kind', 'source'),
    [
        (3, 'OP118', 'A', 'bus', 'bus'),
        (3, 'OP118', 'PR1', 'bus', 'bus'),
        (200, 'OP5050', '777', 'coach', 'coach'),
        (200, 'OP5051', 'UK083', 'coach', 'coach'),
        (3, 'OP5050', '717', 'coach', 'coach'),
        (200, 'OP999', 'X1', 'coach', 'coach'),
        (3, 'OP118', '', None, 'bus'),
        (2, 'OP1', 'GN', None, 'other'),
    ],
)
def test_classify_cambridge(
    route_type: int,
    agency: str,
    short: str,
    kind: str | None,
    source: str,
) -> None:
    """A route's kind and source follow its type and agency."""
    assert classify_cambridge(
        route_type=route_type,
        agency_id=agency,
        short_name=short,
    ) == RouteClass(kind=kind, source=source)


def test_merge_cambridge_trims_then_groups(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Merge cambridge trims then groups."""
    conn = loaded(
        stops=(
            ('0500CCITY227', 0.1240, 52.2050),
            ('0500CCITY486', 0.1244, 52.2054),
            NORWICH,
            STANSTED,
            PARKSIDE,
            MILDENHALL,
        ),
        routes=(
            ('bods:1', 3, 'OP118', '1'),
            ('bods:2', 3, 'OP1', '2'),
            ('bods:c', 200, 'OP5050', '777'),
        ),
        trips=(('t1', 'bods:1'), ('t2', 'bods:2'), ('c1', 'bods:c')),
        calls=(
            ('t1', '0500CCITY227'),
            ('t1', '0500CCITY486'),
            ('t2', 'norwich'),
            ('t2', 'norwich'),
            ('c1', 'stansted'),
            ('c1', 'parkside'),
            ('c1', 'mildenhall'),
        ),
        names={
            '0500CCITY227': 'Drummer St Bus Station',
            '0500CCITY486': 'Drummer Street',
        },
    )
    with caplog.at_level(logging.INFO):
        assert merge_cambridge(conn=conn) == MergeReport(
            dropped=1,
            partial=0,
            overnight=0,
        )
    assert 'kept 2 routes, removed 1 trips, shortened 1 coach trips' in (
        caplog.text
    )
    assert route_ids(conn) == {'bods:1', 'bods:c'}
    assert calls_of(conn=conn, trip='c1') == ['parkside', 'mildenhall']
    assert (
        parents(conn)['0500CCITY227']
        == parents(conn)['0500CCITY486']
        == 'cambridge-drummer-street'
    )


def test_the_source_name_is_the_holiday_source_and_the_route_prefix() -> None:
    """The build's source name is the name the city looks up."""
    name = CAMBRIDGE_SOURCES[0].name
    assert CAMBRIDGE.timetable.holiday_source == name
    assert CAMBRIDGE.alerts.route_prefixes == (f'{name}:',)


def test_the_cambridge_timetable_is_compacted() -> None:
    """Cambridge asks for a vacuumed timetable file."""
    assert CAMBRIDGE.timetable.compact


def test_map_places_drop_localities_and_add_the_named_places() -> None:
    """Cambridge draws no locality and names eleven places of its own."""
    rules = CAMBRIDGE.catalogue.map_places
    assert rules.kinds == {'suburb', 'town', 'village'}
    assert rules.extras == (
        ('Abbey', 52.2127, 0.1586),
        ('Arbury', 52.2228, 0.1167),
        ('Castle', 52.2189, 0.1044),
        ('Coleridge', 52.1904, 0.1505),
        ('East Chesterton', 52.2215, 0.1459),
        ("King's Hedges", 52.2294, 0.1340),
        ('Market', 52.2055, 0.1239),
        ("Queen Edith's", 52.1804, 0.1491),
        ('West Chesterton', 52.2175, 0.1290),
        ('Darwin Green', 52.2251, 0.1006),
        ('Franklin Gardens', 52.2293, 0.1082),
    )
