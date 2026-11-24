"""Tests for catalogue: named trains."""

from collections.abc import Sequence
from contextlib import closing
from dataclasses import replace
from pathlib import Path

from paperroute_sydney.city import SYDNEY, SYDNEY_PROJECTION, SydneyMode
from paperroute_transit.build.catalogue import Entry, kind_rows, load_catalogue
from paperroute_transit.core.modes import ModeSpec, named_line_modes
from tests.build.catalogue.helpers import RouteRow, chooser_rows, tiny_db


def named_trains(*, modes: Sequence[ModeSpec]) -> tuple[ModeSpec, ...]:
    """
    Set named_lines on the train spec.

    Parameters
    ----------
    modes : Sequence[ModeSpec]
        Declared modes.

    Returns
    -------
    tuple[ModeSpec, ...]
        The modes, the train one keyed by long name.
    """
    return tuple(
        replace(spec, named_lines=True)
        if spec.id == SydneyMode.TRAIN
        else spec
        for spec in modes
    )


def test_sydney_has_no_named_lines() -> None:
    """No Sydney mode keys its lines by long name."""
    assert named_line_modes(modes=SYDNEY.modes) == frozenset()


def test_named_train_lines_get_a_row_per_long_name(tmp_path: Path) -> None:
    """One number with two long names makes two rows, ranked by value."""
    with closing(tiny_db(path=tmp_path / 't.db', routes=[
        RouteRow(route_id='a', number='T1', long_name='City to Emu',
                 route_type=2, source='sydneytrains'),
        RouteRow(route_id='b', number='T1', long_name='City to Hornsby',
                 route_type=2, source='sydneytrains'),
        RouteRow(route_id='c', number='T2', long_name='City to Leppington',
                 route_type=2, source='sydneytrains'),
    ])) as conn:
        load_catalogue(
            conn=conn, projection=SYDNEY_PROJECTION, classify=SYDNEY.classify,
            modes=named_trains(modes=SYDNEY.modes),
            rules=replace(SYDNEY.catalogue, line_sort={
                'T2:City to Leppington': 1, 'T1:City to Hornsby': 2,
            }),
        )
        keys = [key for key, in conn.execute(
            "SELECT route_key FROM chooser_routes WHERE kind = 'train'"
            ' ORDER BY sort',
        )]
        rows = chooser_rows(conn=conn, kind='train')
    assert keys == [
        'train:T2:City to Leppington',
        'train:T1:City to Hornsby',
        'train:T1:City to Emu',
    ]
    assert [row[:2] for row in rows] == [
        ('T2', 'City to Leppington'),
        ('T1', 'City to Hornsby'),
        ('T1', 'City to Emu'),
    ]


def test_a_bundle_name_is_exempt(tmp_path: Path) -> None:
    """A named kind keeps its routes' long names over the bundle's."""
    with closing(tiny_db(path=tmp_path / 't.db', routes=[
        RouteRow(route_id='a', number='T1', long_name='City to Emu',
                 route_type=2, source='sydneytrains'),
        RouteRow(route_id='bT1', number='T1', long_name='Bundle name',
                 route_type=2, patterns=()),
    ])) as conn:
        load_catalogue(
            conn=conn, projection=SYDNEY_PROJECTION, classify=SYDNEY.classify,
            modes=named_trains(modes=SYDNEY.modes), rules=SYDNEY.catalogue,
        )
        rows = chooser_rows(conn=conn, kind='train')
    assert [row[:2] for row in rows] == [('T1', 'City to Emu')]


def test_train_lines_in_decision_order(tmp_path: Path) -> None:
    """Sydney lines go T1 to T9, then the intercity lines in order."""
    codes = ['HUN', 'T9', 'SHL', 'T1', 'CCN', 'T5', 'SCO', 'T2', 'BMT']
    routes = [
        RouteRow(route_id=f'st{code}', number=code,
                 long_name=f'City to {code}', route_type=2,
                 agency='NSWTrains', source='sydneytrains')
        for code in codes
    ]
    with closing(tiny_db(path=tmp_path / 't.db', routes=[
        *routes,
        RouteRow(route_id='stT1b', number='T1', long_name='Emu Plains to City',
                 route_type=2, source='sydneytrains'),
        RouteRow(route_id='bT1', number='T1',
                 long_name='T1 North Shore and Western Line', route_type=2,
                 patterns=()),
        RouteRow(route_id='nr', number='', long_name='Non Revenue',
                 route_type=2),
    ])) as conn:
        load_catalogue(
            conn=conn, projection=SYDNEY_PROJECTION, classify=SYDNEY.classify,
            modes=SYDNEY.modes, rules=SYDNEY.catalogue,
        )
        rows = chooser_rows(conn=conn, kind='train')
        keys = [key for key, in conn.execute(
            'SELECT route_key FROM chooser_routes ORDER BY sort',
        )]
    assert [row[0] for row in rows] == [
        'T1', 'T2', 'T5', 'T9', 'BMT', 'CCN', 'SCO', 'SHL', 'HUN',
    ]
    assert rows[0][1] == 'North Shore & Western'
    assert rows[1][1] == 'City to T2'
    assert 'train:T1' in keys


def test_unlisted_train_lines_sort_after_the_listed_ones() -> None:
    """A train line outside T1-T9 and the intercity lines sorts last."""
    rows = kind_rows(entries=[
        Entry(key=f'train:{code}', kind=SydneyMode.TRAIN, number=code,
              name=code, tag='', line='')
        for code in ('T1', 'XPT', 'HUN')
    ], modes=SYDNEY.modes, rules=SYDNEY.catalogue)
    assert {row[4]: row[5] for row in rows} == {
        'T1': 1, 'HUN': 105, 'XPT': 203,
    }


def test_trainlink_is_train_and_coaches_are_coach(tmp_path: Path) -> None:
    """The XPT is a train line and coaches are Coach."""
    with closing(tiny_db(path=tmp_path / 't.db', routes=[
        RouteRow(route_id='nrc', number='NRC', long_name='Sydney to Brisbane',
                 route_type=2, agency='NSWTrains'),
        RouteRow(route_id='c1', number='C1', long_name='Coach',
                 route_type=204),
        RouteRow(route_id='ccn', number='CCN', long_name='CCN Line',
                 route_type=2, agency='NSWTrains'),
    ])) as conn:
        load_catalogue(
            conn=conn, projection=SYDNEY_PROJECTION, classify=SYDNEY.classify,
            modes=SYDNEY.modes, rules=SYDNEY.catalogue,
        )
        kinds = dict(conn.execute('SELECT number, kind FROM chooser_routes'))
    assert kinds == {'NRC': 'train', 'C1': 'coach', 'CCN': 'train'}
