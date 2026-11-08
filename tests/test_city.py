"""Tests for Sydney's city values."""

import itertools
import re
from importlib.resources import files
from typing import Final

import pytest

from paperroute.city import (
    SYDNEY,
    SYDNEY_MODES,
    TRAINLINK,
    SydneyFeed,
    SydneyMode,
    classify_sydney,
)
from paperroute_device.adapters.web_frame import SHELL_WEB
from paperroute_transit.core.modes import RouteClass, mode_problems
from tests.css_rules import css_rules

MODE_IDS: Final[frozenset[str]] = frozenset({
    'bus', 'train', 'metro', 'light_rail', 'ferry', 'school', 'coach',
    'replacement',
})
OLD_REGIONAL: Final[str] = '711'
OLD_INTERCITY: Final[tuple[str, ...]] = ('BMT', 'CCN', 'SCO', 'SHL', 'HUN')
OLD_TRAINLINK: Final[tuple[str, ...]] = ('NRC', 'NRW', 'STH', 'WST')
OLD_LINES: Final[re.Pattern[str]] = re.compile(r'^T[1-9]$')
OLD_EXACT_MODES: Final[dict[int, str]] = {
    2: 'train', 106: 'train', 401: 'metro', 4: 'ferry', 900: 'light_rail',
}
OLD_TRAINLINK_KINDS: Final[dict[int, str]] = {
    106: 'train', 204: 'coach', 205: 'coach',
}
OLD_EXACT_KINDS: Final[dict[int, str]] = {
    **OLD_TRAINLINK_KINDS,
    401: 'metro',
    900: 'light_rail',
    4: 'ferry',
    700: 'bus',
    712: 'school',
    714: 'replacement',
}
ROUTE_TYPES: Final[tuple[int, ...]] = (
    2, 4, 106, 204, 205, 401, 700, 701, 712, 714, 715, 900, 3,
)
AGENCIES: Final[tuple[str | None, ...]] = (None, '711', 'SydneyTrains')
SHORT_NAMES: Final[tuple[str, ...]] = ('', 'T1', 'BMT', 'WST', 'X', '606')
CHIP_FILL: Final[re.Pattern[str]] = re.compile(
    r'^--chip-fill: var\((--mode-[\w-]+)\)$',
)
TOKEN: Final[re.Pattern[str]] = re.compile(r'^(--mode-[\w-]+): (#[0-9a-f]+)$')


def old_mode_for(*, route_type: int, agency_id: str | None) -> str:
    """
    Find a route's feed source as the engine did before cities.

    Parameters
    ----------
    route_type : int
        GTFS route_type.
    agency_id : str | None
        GTFS agency_id.

    Returns
    -------
    str
        Feed source.
    """
    if agency_id == OLD_REGIONAL:
        return 'regional'
    if route_type in OLD_EXACT_MODES:
        return OLD_EXACT_MODES[route_type]
    if 700 <= route_type < 800:
        return 'bus'
    return 'coach' if 200 <= route_type < 300 else 'other'


def old_kind_for(
    *, route_type: int, agency_id: str | None, short_name: str,
) -> str | None:
    """
    Sort a route into a mode as the engine did before cities.

    Parameters
    ----------
    route_type : int
        GTFS route_type.
    agency_id : str | None
        GTFS agency_id.
    short_name : str
        Route short name.

    Returns
    -------
    str | None
        Mode id, or None.
    """
    if not short_name:
        return None
    if agency_id == OLD_REGIONAL:
        if route_type == 2:
            return 'train' if short_name in OLD_TRAINLINK else None
        return OLD_TRAINLINK_KINDS.get(route_type)
    if route_type == 2:
        known = (
            OLD_LINES.match(short_name)
            or short_name in OLD_INTERCITY
            or short_name in OLD_TRAINLINK
        )
        return 'train' if known else None
    return OLD_EXACT_KINDS.get(route_type)


def mode_tokens() -> dict[str, str]:
    """
    Read the kit's mode colour tokens.

    Returns
    -------
    dict[str, str]
        Colour by `--mode-*` token, from `live.css`'s `:root`.
    """
    css = (SHELL_WEB / 'live.css').read_text(encoding='utf-8')
    return {
        found.group(1): found.group(2)
        for rule in css_rules(css=css)
        if rule.selector == ':root' and not rule.context
        for line in rule.body
        if (found := TOKEN.match(line))
    }


def chip_tokens() -> dict[str, str]:
    """
    Read which token each board mode class fills its chip with.

    Returns
    -------
    dict[str, str]
        `--mode-*` token by `.mode-*` class.
    """
    css = (files('paperroute') / 'assets' / 'web' / 'board.css').read_text(
        encoding='utf-8',
    )
    return {
        selector.strip(): found.group(1)
        for rule in css_rules(css=css)
        for line in rule.body
        if (found := CHIP_FILL.match(line))
        for selector in rule.selector.split(',')
    }


def test_sydney_city_values() -> None:
    """Sydney runs on Sydney time and covers today's NSW box."""
    assert SYDNEY.zone.key == 'Australia/Sydney'
    assert SYDNEY.bbox == (140.99, -37.51, 153.64, -28.15)


def test_sydney_modes_are_sound() -> None:
    """Sydney's declared modes have no problems."""
    assert not mode_problems(modes=SYDNEY_MODES)


def test_sydney_ids_are_old_values() -> None:
    """Sydney's mode ids are the kind values pins already store."""
    assert {spec.id for spec in SYDNEY_MODES} == {
        member.value for member in SydneyMode
    }
    assert {spec.id for spec in SYDNEY_MODES} == MODE_IDS
    assert SYDNEY.mode_ids == MODE_IDS


def test_city_finds_its_modes() -> None:
    """The city looks modes up by id; None and unknown ids give None."""
    found = SYDNEY.mode(mode_id='replacement')
    assert found is not None
    assert (found.under, found.replaces) == ('bus', 'train')
    assert SYDNEY.mode(mode_id=None) is None
    assert SYDNEY.mode(mode_id='tram') is None


def test_classifier_matches_old_functions() -> None:
    """Sydney's classifier gives what kind_for and mode_for gave."""
    for route_type, agency_id, short_name in itertools.product(
        ROUTE_TYPES, AGENCIES, SHORT_NAMES,
    ):
        assert classify_sydney(
            route_type=route_type,
            agency_id=agency_id,
            short_name=short_name,
        ) == RouteClass(
            kind=old_kind_for(
                route_type=route_type,
                agency_id=agency_id,
                short_name=short_name,
            ),
            source=old_mode_for(route_type=route_type, agency_id=agency_id),
        ), (route_type, agency_id, short_name)


def test_chip_colours_match_kit_css() -> None:
    """Each mode's chip is the colour its board class takes from the kit."""
    tokens = mode_tokens()
    chips = chip_tokens()
    assert {
        spec.id: spec.chip for spec in SYDNEY_MODES
    } == {
        spec.id: tokens[chips[f".mode-{spec.id.replace('_', '-')}"]]
        for spec in SYDNEY_MODES
    }


@pytest.mark.parametrize(('route_type', 'agency_id', 'source'), [
    (2, 'x0001', SydneyFeed.TRAIN),
    (2, 'NSWTrains', SydneyFeed.TRAIN),
    (401, 'SMNW', SydneyFeed.METRO),
    (4, None, SydneyFeed.FERRY),
    (900, None, SydneyFeed.LIGHT_RAIL),
    (700, '2436', SydneyFeed.BUS),
    (712, None, SydneyFeed.BUS),
    (204, None, SydneyFeed.COACH),
    (106, '711', SydneyFeed.REGIONAL),
    (204, '711', SydneyFeed.REGIONAL),
    (1, None, SydneyFeed.OTHER),
])
def test_feed_sources_by_route_type(
    route_type: int, agency_id: str | None, source: SydneyFeed,
) -> None:
    """GTFS route types and NSW TrainLink's agency map to feed sources."""
    assert classify_sydney(
        route_type=route_type, agency_id=agency_id, short_name='X',
    ).source == source


@pytest.mark.parametrize(('route_type', 'agency_id', 'short_name', 'kind'), [
    (2, 'SydneyTrains', 'T1', SydneyMode.TRAIN),
    (2, 'SydneyTrains', 'CCN', SydneyMode.TRAIN),
    (2, 'NSWTrains', 'NRC', SydneyMode.TRAIN),
    (2, 'NSWTrains', 'NRW', SydneyMode.TRAIN),
    (2, 'NSWTrains', 'STH', SydneyMode.TRAIN),
    (2, 'NSWTrains', 'WST', SydneyMode.TRAIN),
    (2, 'NSWTrains', 'XPT', None),
    (2, 'SydneyTrains', '', None),
    (700, '2436', '', None),
    (106, '711', '31', SydneyMode.TRAIN),
    (204, '711', '700', SydneyMode.COACH),
    (205, '711', '701', SydneyMode.COACH),
    (204, None, '700', SydneyMode.COACH),
    (700, '711', '480', None),
    (2, '711', 'T1', None),
    (401, 'SMNW', 'M1', SydneyMode.METRO),
    (900, None, 'L1', SydneyMode.LIGHT_RAIL),
    (4, None, 'F1', SydneyMode.FERRY),
    (700, '2436', '606', SydneyMode.BUS),
    (712, '2436', '2606', SydneyMode.SCHOOL),
    (714, '2436', 'RTTA', SydneyMode.REPLACEMENT),
    (715, '2436', 'OD1', None),
])
def test_modes_by_route_type(
    route_type: int, agency_id: str | None, short_name: str,
    kind: SydneyMode | None,
) -> None:
    """Route types, agencies and line names sort into chooser modes."""
    assert classify_sydney(
        route_type=route_type, agency_id=agency_id, short_name=short_name,
    ).kind == kind


def test_bundle_trainlink_services_are_train() -> None:
    """The bundle's numbered XPT services are trains, not coaches."""
    assert classify_sydney(
        route_type=106, agency_id='711', short_name='31',
    ).kind == SydneyMode.TRAIN


def test_trainlink_lines_are_train() -> None:
    """NRC, NRW, STH and WST are Train lines."""
    assert {
        code: classify_sydney(
            route_type=2, agency_id='NSWTrains', short_name=code,
        ).kind
        for code in TRAINLINK
    } == dict.fromkeys(TRAINLINK, SydneyMode.TRAIN)


def test_merged_trainlink_trains_are_train() -> None:
    """A trains-feed WST trip that took the bundle's agency 711 is Train."""
    assert classify_sydney(
        route_type=2, agency_id='711', short_name='WST',
    ).kind == SydneyMode.TRAIN


def test_trainlink_coaches_are_coach() -> None:
    """Route types 204 and 205 of agency 711 are coaches."""
    assert [
        classify_sydney(
            route_type=route_type, agency_id='711', short_name='C1',
        ).kind
        for route_type in (204, 205)
    ] == [SydneyMode.COACH, SydneyMode.COACH]


def test_line_group_matches_today() -> None:
    """Sydney's line groups are the chooser's groups from before the city."""
    numbers = ('T1', 'T9', 'F10', 'BMT', 'HUN', 'WST', 'NRC', 'F11', 'X')
    assert [
        SYDNEY.catalogue.line_group(number=number) for number in numbers
    ] == [0, 0, 0, 1, 1, 2, 2, 3, 3]
