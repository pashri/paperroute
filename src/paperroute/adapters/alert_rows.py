"""Look up the routes and stops an alert feed names."""

from collections.abc import Collection
from typing import Final

from paperroute.adapters.calendar_rows import Fetch, FetchIn
from paperroute.core.alert_model import AlertPlaces

ROUTE_PREFIXES: Final[tuple[str, ...]] = (
    'sydneytrains:',
    'metro:',
    'bundle:',
)
ROUTE_NAMES: Final[str] = (
    'SELECT route_id, short_name FROM routes WHERE route_id IN ({marks})'
)
SHORT_NAMES: Final[str] = (
    'SELECT DISTINCT short_name FROM routes WHERE short_name IN ({marks})'
)
STOP_FAMILY: Final[str] = (
    'SELECT stop_id, parent_station FROM stops WHERE stop_id IN ({marks})'
)
STOP_CHILDREN: Final[str] = (
    'SELECT stop_id, parent_station FROM stops '
    'WHERE parent_station IN ({marks})'
)
METRO_ROUTE: Final[str] = (
    'SELECT DISTINCT short_name FROM routes WHERE type = 401'
)


def suffix_of(*, route_id: str) -> str | None:
    """
    Take the text after the last underscore of a route ID.

    Parameters
    ----------
    route_id : str
        Route ID from a feed.

    Returns
    -------
    str | None
        The suffix, or None when there is no underscore or nothing
        after it.
    """
    head, found, tail = route_id.rpartition('_')
    return tail if head and found and tail else None


def read_routes(
    *, route_ids: Collection[str], fetch_in: FetchIn
) -> dict[str, str]:
    """
    Resolve feed route IDs to route short names.

    Parameters
    ----------
    route_ids : Collection[str]
        Route IDs from a feed.
    fetch_in : FetchIn
        Runs a query with an IN list and returns its rows.

    Returns
    -------
    dict[str, str]
        Short name by feed ID. An ID resolves to the first of its
        sydneytrains, metro and bundle forms that is a route; failing
        that, to the text after its last underscore when some route has
        that short name. Other IDs are absent.
    """
    found = prefixed_routes(route_ids=route_ids, fetch_in=fetch_in)
    rest = {
        route_id: suffix
        for route_id in route_ids
        if route_id not in found
        and (suffix := suffix_of(route_id=route_id))
    }
    rows = fetch_in(SHORT_NAMES, set(rest.values()))
    known = {str(row[0]) for row in rows}
    return found | {
        route_id: suffix
        for route_id, suffix in rest.items()
        if suffix in known
    }


def prefixed_routes(
    *, route_ids: Collection[str], fetch_in: FetchIn
) -> dict[str, str]:
    """
    Resolve feed route IDs through their prefixed forms.

    Parameters
    ----------
    route_ids : Collection[str]
        Route IDs from a feed.
    fetch_in : FetchIn
        Runs a query with an IN list and returns its rows.

    Returns
    -------
    dict[str, str]
        Short name by feed ID, from the first of its sydneytrains, metro
        and bundle forms that is a route; other IDs are absent.
    """
    candidates = [
        prefix + route_id
        for route_id in route_ids
        for prefix in ROUTE_PREFIXES
    ]
    names = {
        str(row[0]): str(row[1]) for row in fetch_in(ROUTE_NAMES, candidates)
    }
    return {
        route_id: next(
            names[prefix + route_id]
            for prefix in ROUTE_PREFIXES
            if prefix + route_id in names
        )
        for route_id in route_ids
        if any(prefix + route_id in names for prefix in ROUTE_PREFIXES)
    }


def read_families(
    *, stop_ids: Collection[str], fetch_in: FetchIn
) -> dict[str, frozenset[str]]:
    """
    Find each stop's family: itself, its parent and its children.

    Parameters
    ----------
    stop_ids : Collection[str]
        Stop IDs from a feed.
    fetch_in : FetchIn
        Runs a query with an IN list and returns its rows.

    Returns
    -------
    dict[str, frozenset[str]]
        Family by stop ID. A known stop's family holds itself; unknown
        IDs are absent.
    """
    family: dict[str, set[str]] = {}
    for stop, parent in fetch_in(STOP_FAMILY, stop_ids):
        members = family.setdefault(str(stop), {str(stop)})
        if parent:
            members.add(str(parent))
    for stop, parent in fetch_in(STOP_CHILDREN, stop_ids):
        family.setdefault(str(parent), {str(parent)}).add(str(stop))
    return {stop: frozenset(members) for stop, members in family.items()}


def read_metro(*, fetch: Fetch) -> str | None:
    """
    Read the short name of the timetable's only metro route.

    Parameters
    ----------
    fetch : Fetch
        Runs one query and returns its rows.

    Returns
    -------
    str | None
        The short name, or None unless exactly one metro route exists.
    """
    rows = fetch(METRO_ROUTE)
    return str(rows[0][0]) if len(rows) == 1 else None


def alert_places(
    *,
    route_ids: Collection[str],
    stop_ids: Collection[str],
    fetch: Fetch,
    fetch_in: FetchIn,
) -> AlertPlaces:
    """
    Look up what the timetable knows about an alert feed's IDs.

    Parameters
    ----------
    route_ids : Collection[str]
        Route IDs from the feed.
    stop_ids : Collection[str]
        Stop IDs from the feed.
    fetch : Fetch
        Runs one query and returns its rows.
    fetch_in : FetchIn
        Runs a query with an IN list and returns its rows.

    Returns
    -------
    AlertPlaces
        Route short names, stop families and the metro line.
    """
    return AlertPlaces(
        routes=read_routes(route_ids=route_ids, fetch_in=fetch_in),
        stops=read_families(stop_ids=stop_ids, fetch_in=fetch_in),
        metro=read_metro(fetch=fetch),
    )
