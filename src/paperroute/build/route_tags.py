"""Tag routes that share a number with a short place or word."""

import logging
import math
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from paperroute.core.geo import Point, to_metres
from paperroute.core.model import RouteKind

logger = logging.getLogger(__name__)

CENTRES: Final[tuple[tuple[str, float, float, float], ...]] = (
    ('Sydney', 151.207, -33.870, 55),
    ('Newcastle', 151.750, -32.930, 40),
    ('C Coast', 151.340, -33.430, 30),
    ('Wollongong', 150.890, -34.420, 30),
    ('Blue Mtns', 150.400, -33.700, 25),
)  # label, lon, lat, radius in km
PLACE_SUFFIX: Final[re.Pattern[str]] = re.compile(
    r'\s+(City Centre|Town Centre|CBD|Station|Interchange|Mall|City|Park Ave'
    r'|Shopping Centre|Shops|Market Square|Manning Mall|East|West|North'
    r'|South|CBD)$',
)
PLACE_SHORT: Final[dict[str, str]] = {
    'Port Macquarie': 'Port Mac',
    'Coffs Harbour': 'Coffs',
    'Wagga Wagga': 'Wagga',
    'Batemans Bay': 'Batemans',
    'Raymond Terrace': 'Raymond T',
    'Soldiers Point': 'Soldiers P',
    'Muswellbrook': 'Muswellbk',
    'Murwillumbah': 'Murwillum',
    'Queanbeyan': 'Queanbeyan',
    'University of Wollongong': 'UOW',
    'City Martin Place': 'City',
    'City Wynyard': 'City',
    'City QVB': 'City',
    'Broken Hill': 'Broken Hl',
    'Campbelltown': 'Campbelltn',
    'Stockland Forster': 'Forster',
    'QEII Square': 'Albury',
    'Orana': 'Dubbo',
    'Green Hills': 'Maitland',
    'Park Beach Plaza': 'Coffs',
    'Toormina Gardens': 'Coffs',
    'Sunnyside': 'Murwillum',
}
TAG_MAX: Final[int] = 10
LEVELS: Final[int] = 4  # region, first place, last place, via place
WORD: Final[re.Pattern[str]] = re.compile(r'[^\W_]+')
UNKNOWN_TOWN: Final[str] = '?'


@dataclass(frozen=True)
class CatalogueRoute:
    """
    One chooser route: every route ID of a kind, number and long name.

    Attributes
    ----------
    route_key : str
        'kind:number:long name'.
    kind : RouteKind
        Kind.
    number : str
        Route short name.
    long_name : str
        Route long name.
    agency : str
        The operator running the most trips.
    middle : Point | None
        Median station point in metres; None without station positions.
    """

    route_key: str
    kind: RouteKind
    number: str
    long_name: str
    agency: str
    middle: Point | None


def end_places(*, long_name: str) -> list[str]:
    """
    Pull the place names out of a long name such as 'A to B via C'.

    Parameters
    ----------
    long_name : str
        Route long name.

    Returns
    -------
    list[str]
        The places before any 'via', '(', ' or ' or ' and ', cleaned of
        suffixes; 'Unknown' and blanks are left out.
    """
    trimmed = re.split(r' via |\(| or | and ', long_name)[0]
    return [
        PLACE_SUFFIX.sub('', part.strip()).strip()
        for part in trimmed.split(' to ')
        if part.strip() and part.strip() != 'Unknown'
    ]


def via_place(*, long_name: str) -> str:
    """
    Find the place a long name says the route runs via.

    Parameters
    ----------
    long_name : str
        Route long name.

    Returns
    -------
    str
        The text after ' via ' up to any '(', cleaned of suffixes; '' when
        there is none.
    """
    _, via, rest = long_name.partition(' via ')
    place = PLACE_SUFFIX.sub('', rest.split('(')[0].strip()).strip()
    return place if via else ''


def short_tag(*, place: str, other: str | None = None) -> str:
    """
    Shorten a place to a tag, keeping it apart from another place.

    Parameters
    ----------
    place : str
        Place name.
    other : str | None
        A different place whose cut might read the same.

    Returns
    -------
    str
        The PLACE_SHORT form or the name when it fits TAG_MAX, else its
        first nine characters and '.'; when that equals the other's cut
        although the names differ, its first four characters, '.', then
        its last five.
    """
    short = PLACE_SHORT.get(place, place)
    cut = short
    if len(short) > TAG_MAX:
        cut = short[:TAG_MAX - 1].rstrip() + '.'
    if other is None or other == place or short_tag(place=other) != cut:
        return cut
    return f'{short[:4]}.{short[-5:]}'


def agency_towns(*, routes: Sequence[CatalogueRoute]) -> dict[str, str]:
    """
    Name each operator after the place its routes most often end at.

    Parameters
    ----------
    routes : Sequence[CatalogueRoute]
        Routes.

    Returns
    -------
    dict[str, str]
        Place by operator ID; operators with no places are missing.
    """
    ends: dict[str, Counter[str]] = defaultdict(Counter)
    for route in routes:
        ends[route.agency].update(end_places(long_name=route.long_name))
    return {
        agency: places.most_common(1)[0][0]
        for agency, places in ends.items()
        if places
    }


def centre_of(*, middle: Point) -> str | None:
    """
    Find the big centre a point belongs to, if any.

    Parameters
    ----------
    middle : Point
        Position in metres.

    Returns
    -------
    str | None
        The centre whose radius holds the point, nearest by share of
        radius, or None outside them all.
    """
    reach = []
    for label, lon, lat, radius in CENTRES:
        reached = math.dist(middle, to_metres(lon=lon, lat=lat))
        if (share := reached / (radius * 1000)) < 1:
            reach.append((share, label))
    return min(reach)[1] if reach else None


def level_values(
    *, route: CatalogueRoute, towns: Mapping[str, str],
) -> tuple[str, str, str, str]:
    """
    Give a route's candidate tags, level by level.

    Parameters
    ----------
    route : CatalogueRoute
        Route.
    towns : Mapping[str, str]
        Place by operator ID.

    Returns
    -------
    tuple[str, str, str, str]
        Region (centre, else operator town, else '?'), first place, last
        place and via place; '' where there is none.
    """
    centre = None if route.middle is None else centre_of(middle=route.middle)
    places = end_places(long_name=route.long_name) or ['']
    return (
        centre or towns.get(route.agency, UNKNOWN_TOWN),
        places[0],
        places[-1],
        via_place(long_name=route.long_name),
    )


def twin_of(*, place: str, places: Iterable[str]) -> str | None:
    """
    Find another place whose cut reads the same as this one's.

    Parameters
    ----------
    place : str
        Place.
    places : Iterable[str]
        Places to look through.

    Returns
    -------
    str | None
        The first different place with the same cut, if any.
    """
    cut = short_tag(place=place)
    return next(
        (
            other
            for other in places
            if other != place and short_tag(place=other) == cut
        ),
        None,
    )


def level_tags(
    *, values: Mapping[str, str], given: set[str],
) -> dict[str, str]:
    """
    Tag the routes whose value at one level tells them apart.

    Parameters
    ----------
    values : Mapping[str, str]
        Each untagged route's value at the level, by route key.
    given : set[str]
        Tags already given in the group.

    Returns
    -------
    dict[str, str]
        Tags, for routes whose value is usable and whose tag is unique.
    """
    tags = {
        key: short_tag(
            place=value, other=twin_of(place=value, places=values.values()),
        )
        for key, value in values.items()
        if value not in ('', UNKNOWN_TOWN)
    }
    seen = Counter(tags.values())
    return {
        key: tag
        for key, tag in tags.items()
        if seen[tag] == 1 and tag not in given
    }


def differing_word(
    *, words: tuple[str, ...], others: Sequence[tuple[str, ...]],
    taken: set[str],
) -> str | None:
    """
    Find the first word unlike the other routes' words in its place.

    Parameters
    ----------
    words : tuple[str, ...]
        One route's words.
    others : Sequence[tuple[str, ...]]
        The other routes' words.
    taken : set[str]
        Tags already used.

    Returns
    -------
    str | None
        Tag of the first word that differs from every other route's word
        at the same position and is not taken, if any.
    """
    return next(
        (
            short_tag(place=word)
            for index, word in enumerate(words)
            if all(other[index:index + 1] != (word,) for other in others)
            and short_tag(place=word) not in taken
        ),
        None,
    )


def free_word(*, words: tuple[str, ...], taken: set[str], number: str) -> str:
    """
    Pick a route's first word whose tag is not yet used.

    Parameters
    ----------
    words : tuple[str, ...]
        One route's words.
    taken : set[str]
        Tags already used.
    number : str
        Route number, the tag when the long name has no words.

    Returns
    -------
    str
        That tag; else the first word's tag (shared), or the number with
        no words.
    """
    tags = [short_tag(place=word) for word in words] or [number]
    return next((tag for tag in tags if tag not in taken), tags[0])


def differing_picks(
    *, units: Sequence[tuple[str, ...]], taken: set[str],
) -> dict[tuple[str, ...], str]:
    """
    Pick for each name a word the other names lack, where there is one.

    Parameters
    ----------
    units : Sequence[tuple[str, ...]]
        Each distinct name's words.
    taken : set[str]
        Words already used; each pick is added.

    Returns
    -------
    dict[tuple[str, ...], str]
        The word picked for each name that has one.
    """
    picks: dict[tuple[str, ...], str] = {}
    for unit in units:
        others = [other for other in units if other != unit]
        if pick := differing_word(words=unit, others=others, taken=taken):
            picks[unit] = pick
            taken.add(pick)
    return picks


def word_tags(
    *, routes: Sequence[CatalogueRoute], given: set[str],
) -> dict[str, str]:
    """
    Tag routes alike at every level by a word of their long names.

    Routes with the same words share one tag.

    Parameters
    ----------
    routes : Sequence[CatalogueRoute]
        Routes left untagged.
    given : set[str]
        Tags already given in the group.

    Returns
    -------
    dict[str, str]
        Tags by route key.
    """
    words = {
        route.route_key: tuple(WORD.findall(route.long_name))
        for route in routes
    }
    units = list(dict.fromkeys(words.values()))
    taken = set(given)
    picks = differing_picks(units=units, taken=taken)
    for unit in (unit for unit in units if unit not in picks):
        picks[unit] = free_word(
            words=unit, taken=taken, number=routes[0].number,
        )
        taken.add(picks[unit])
    return {key: picks[unit] for key, unit in words.items()}


def clashes(
    *, tags: Mapping[str, str], left: Sequence[CatalogueRoute],
) -> list[str]:
    """
    List the routes to warn about once a group is tagged.

    Parameters
    ----------
    tags : Mapping[str, str]
        Tags by route key.
    left : Sequence[CatalogueRoute]
        Routes no level told apart.

    Returns
    -------
    list[str]
        Route keys in the order tagged: every route sharing a tag, and the
        routes left when more than one is left; empty when none.
    """
    seen = Counter(tags.values())
    named = {route.route_key for route in left} if len(left) > 1 else set()
    return [
        key
        for key, tag in tags.items()
        if key in named or seen[tag] > 1
    ]


def group_tags(
    *, group: Sequence[CatalogueRoute], towns: Mapping[str, str],
) -> dict[str, str]:
    """
    Tag the routes of one repeated number.

    Each level (region, first place, last place, via place) tags the
    routes it tells apart; any left are warned about and tagged by words.

    Parameters
    ----------
    group : Sequence[CatalogueRoute]
        Routes sharing a kind and number.
    towns : Mapping[str, str]
        Place by operator ID.

    Returns
    -------
    dict[str, str]
        Tags by route key.
    """
    values = {
        route.route_key: level_values(route=route, towns=towns)
        for route in group
    }
    tags: dict[str, str] = {}
    for level in range(LEVELS):
        tags.update(level_tags(
            values={
                key: value[level]
                for key, value in values.items()
                if key not in tags
            },
            given=set(tags.values()),
        ))
    left = [route for route in group if route.route_key not in tags]
    if left:
        tags.update(word_tags(routes=left, given=set(tags.values())))
    if clashing := clashes(tags=tags, left=left):
        logger.warning(
            'route tags clash after every level: %s', ', '.join(clashing),
        )
    return tags


def tags_of(
    *, routes: Sequence[CatalogueRoute], towns: Mapping[str, str],
) -> dict[str, str]:
    """
    Tag every route whose kind and number repeat.

    Parameters
    ----------
    routes : Sequence[CatalogueRoute]
        Merged routes.
    towns : Mapping[str, str]
        Place by operator ID.

    Returns
    -------
    dict[str, str]
        Tags by route key; routes with a unique number are missing.
    """
    groups: dict[tuple[RouteKind, str], list[CatalogueRoute]] = defaultdict(
        list,
    )
    for route in routes:
        groups[(route.kind, route.number)].append(route)
    tags: dict[str, str] = {}
    for group in groups.values():
        if len(group) > 1:
            tags.update(group_tags(group=group, towns=towns))
    return tags
