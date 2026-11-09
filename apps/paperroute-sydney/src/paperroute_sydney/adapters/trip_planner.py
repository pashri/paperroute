"""Ask the TfNSW Trip Planner for journeys."""

import logging
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime
from http import HTTPStatus
from typing import Final

import aiohttp

from paperroute_device.adapters.rate_limit import StartLimiter
from paperroute_device.adapters.secrets_file import SecretsFile
from paperroute_sydney.adapters.tfnsw import (
    BASE_URL,
    key_path_from_env,
    refusal_detail,
    tfnsw_limiter,
)
from paperroute_sydney.core.trip_search import (
    Journey,
    JourneyLeg,
    PlannerBusy,
    PlannerUnavailable,
    TripSearch,
)

TRIP_PATH: Final[str] = 'v1/tp/trip'
WALK_CLASSES: Final[frozenset[int]] = frozenset({99, 100})
SCHOOL_CLASS: Final[int] = 11
FIXED_PARAMS: Final[dict[str, str]] = {
    'outputFormat': 'rapidJSON',
    'coordOutputFormat': 'EPSG:4326',
    'type_origin': 'any',
    'type_destination': 'any',
    'calcNumberOfTrips': '5',
    'TfNSWTR': 'true',
    'version': '10.2.1.42',
}
FetchJson = Callable[[str, Mapping[str, str]], Awaitable[object]]

logger = logging.getLogger(__name__)


def trip_params(
    *, origin_id: str, destination_id: str, search: TripSearch,
) -> dict[str, str]:
    """
    Build the query for a trip request.

    Parameters
    ----------
    origin_id : str
        Trip Planner ID of the start.
    destination_id : str
        Trip Planner ID of the end.
    search : TripSearch
        When to leave or arrive.

    Returns
    -------
    dict[str, str]
        The query parameters; arrive-by also sets
        `itdTripDateTimeDepArr=arr`.
    """
    params = {
        **FIXED_PARAMS,
        'depArrMacro': 'arr' if search.arrive else 'dep',
        'itdDate': search.when.strftime('%Y%m%d'),
        'itdTime': search.when.strftime('%H%M'),
        'name_origin': origin_id,
        'name_destination': destination_id,
    }
    if search.arrive:
        params['itdTripDateTimeDepArr'] = 'arr'
    return params


def is_walk(*, leg: Mapping[str, object]) -> bool:
    """
    Say whether a leg is walking.

    Parameters
    ----------
    leg : Mapping[str, object]
        One leg of a journey.

    Returns
    -------
    bool
        True for a leg with product class 99 or 100, or with no route
        name.

    Raises
    ------
    KeyError
        If the leg has no `transportation` entry.
    """
    transport = leg['transportation']
    assert isinstance(transport, dict)
    return (
        product_class(transport=transport) in WALK_CLASSES
        or 'disassembledName' not in transport
    )


def product_class(*, transport: Mapping[str, object]) -> object:
    """
    Read a leg's product class.

    Parameters
    ----------
    transport : Mapping[str, object]
        The leg's `transportation` entry.

    Returns
    -------
    object
        The class, or None when the leg has no product.
    """
    product = transport.get('product')
    return product.get('class') if isinstance(product, dict) else None


def parse_leg(*, leg: Mapping[str, object]) -> JourneyLeg:
    """
    Read one transit leg.

    Parameters
    ----------
    leg : Mapping[str, object]
        One leg of a journey.

    Returns
    -------
    JourneyLeg
        The ride.

    Raises
    ------
    KeyError
        If a field is missing.
    TypeError
        If a field has the wrong shape.
    ValueError
        If a time is not a time.
    """
    origin, destination = leg['origin'], leg['destination']
    transport = leg['transportation']
    assert isinstance(origin, dict)
    assert isinstance(destination, dict)
    assert isinstance(transport, dict)
    return JourneyLeg(
        origin_id=str(origin['id']),
        destination_id=str(destination['id']),
        route=str(transport['disassembledName']),
        departs=datetime.fromisoformat(origin['departureTimePlanned']),
        arrives=datetime.fromisoformat(destination['arrivalTimePlanned']),
        school=product_class(transport=transport) == SCHOOL_CLASS,
    )


def parse_journey(*, journey: object) -> Journey | None:
    """
    Read one journey.

    Parameters
    ----------
    journey : object
        One entry of the reply's `journeys`.

    Returns
    -------
    Journey | None
        The journey without its walking legs; None when it is malformed
        or has no transit leg.
    """
    try:
        assert isinstance(journey, dict)
        legs = tuple(
            parse_leg(leg=leg)
            for leg in journey['legs']
            if not is_walk(leg=leg)
        )
    except (AssertionError, KeyError, TypeError, ValueError):
        return None
    return Journey(legs=legs) if legs else None


def parse_journeys(*, data: object) -> tuple[Journey, ...]:
    """
    Read a Trip Planner reply.

    Parameters
    ----------
    data : object
        The reply's JSON.

    Returns
    -------
    tuple[Journey, ...]
        The journeys that parse, in reply order; a reply of the wrong
        shape gives none.
    """
    journeys = data.get('journeys') if isinstance(data, dict) else None
    if not isinstance(journeys, list):
        return ()
    parsed = (parse_journey(journey=journey) for journey in journeys)
    return tuple(journey for journey in parsed if journey is not None)


def check_status(*, status: int) -> None:
    """
    Refuse any Trip Planner reply but 200.

    Parameters
    ----------
    status : int
        The HTTP status.

    Raises
    ------
    PlannerBusy
        On HTTP 429.
    PlannerUnavailable
        On any other status but 200.
    """
    if status == HTTPStatus.TOO_MANY_REQUESTS:
        raise PlannerBusy(f'HTTP {status}')
    if status != HTTPStatus.OK:
        raise PlannerUnavailable(f'HTTP {status}')


class TfnswTripPlanner:
    """
    The TfNSW Trip Planner; the API key is sent but never logged.

    Parameters
    ----------
    session_factory : Callable[[], aiohttp.ClientSession]
        Makes the HTTP session on first use.
    key : str
        TfNSW Open Data API key.
    fetch_json : FetchJson | None
        Gets JSON from a URL and query; the default makes the HTTP call.
    base_url : str
        API root, ending in '/'.
    timeout_s : float
        Total time allowed per request, in seconds.
    limiter : StartLimiter | None
        Spaces out request starts; none means no limit.
    """

    def __init__(
        self,
        *,
        session_factory: Callable[[], aiohttp.ClientSession],
        key: str,
        fetch_json: FetchJson | None = None,
        base_url: str = BASE_URL,
        timeout_s: float = 10.0,
        limiter: StartLimiter | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.session: aiohttp.ClientSession | None = None
        self.key = key
        self.fetch_json = fetch_json or self.get_json
        self.base_url = base_url
        self.timeout = aiohttp.ClientTimeout(total=timeout_s)
        self.limiter = limiter

    async def get_json(self, url: str, params: Mapping[str, str]) -> object:
        """
        Get JSON over HTTP.

        Parameters
        ----------
        url : str
            Address to fetch.
        params : Mapping[str, str]
            Query parameters.

        Returns
        -------
        object
            The parsed body.

        Raises
        ------
        PlannerBusy
            On HTTP 429.
        PlannerUnavailable
            On a connection error, a timeout, a bad body or any status
            other than 200.

        Notes
        -----
        On a 429 or 403 it logs TfNSW's reason from the response alone;
        the request headers hold the API key and are never logged.
        """
        self.session = self.session or self.session_factory()
        if self.limiter:
            await self.limiter.wait()
        try:
            async with self.session.get(
                url,
                params=params,
                headers={'Authorization': f'apikey {self.key}'},
                timeout=self.timeout,
            ) as response:
                detail = await refusal_detail(response=response)
                if detail:
                    logger.warning(
                        'trip planner refused: HTTP %s',
                        response.status,
                        extra={'refusal': detail},
                    )
                check_status(status=response.status)
                return await response.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError) as error:
            raise PlannerUnavailable(type(error).__name__) from error

    async def close(self) -> None:
        """Close the HTTP session, if one was made."""
        if self.session is not None:
            await self.session.close()
            self.session = None

    async def trips(
        self, *, origin_id: str, destination_id: str, search: TripSearch,
    ) -> tuple[Journey, ...]:
        """
        Ask for journeys.

        Parameters
        ----------
        origin_id : str
            Trip Planner ID of the start.
        destination_id : str
            Trip Planner ID of the end.
        search : TripSearch
            When to leave or arrive.

        Returns
        -------
        tuple[Journey, ...]
            The journeys found, without walking legs.

        Raises
        ------
        PlannerUnavailable
            On a transport error, a timeout or a bad status.
        """
        params = trip_params(
            origin_id=origin_id,
            destination_id=destination_id,
            search=search,
        )
        data = await self.fetch_json(self.base_url + TRIP_PATH, params)
        return parse_journeys(data=data)


class KeyedTripPlanner:
    """
    A Trip Planner that reads the API key before each request.

    Parameters
    ----------
    key : Callable[[], str | None]
        Gives the current key, or None when there is none.
    planner : TfnswTripPlanner
        The planner that makes the requests.
    """

    def __init__(
        self, *, key: Callable[[], str | None], planner: TfnswTripPlanner,
    ) -> None:
        self.key = key
        self.planner = planner

    async def close(self) -> None:
        """Close the planner's HTTP session."""
        await self.planner.close()

    async def trips(
        self, *, origin_id: str, destination_id: str, search: TripSearch,
    ) -> tuple[Journey, ...]:
        """
        Ask for journeys with the current key.

        Parameters
        ----------
        origin_id : str
            Trip Planner ID of the start.
        destination_id : str
            Trip Planner ID of the end.
        search : TripSearch
            When to leave or arrive.

        Returns
        -------
        tuple[Journey, ...]
            The journeys found, without walking legs.

        Raises
        ------
        PlannerUnavailable
            When there is no key, or the request fails.
        """
        key = self.key()
        if key is None:
            raise PlannerUnavailable('no key')
        self.planner.key = key
        return await self.planner.trips(
            origin_id=origin_id, destination_id=destination_id, search=search,
        )


def trip_planner_for(*, live: bool) -> KeyedTripPlanner | None:
    """
    Make the Trip Planner for live feeds.

    Parameters
    ----------
    live : bool
        True when the device runs on live feeds.

    Returns
    -------
    KeyedTripPlanner | None
        A planner reading the key file named by PAPERROUTE_API_KEY_FILE
        at each request and sharing the feeds' limiter; None for replayed
        feeds.
    """
    if not live:
        return None
    return KeyedTripPlanner(
        key=SecretsFile(path=key_path_from_env()).read,
        planner=TfnswTripPlanner(
            session_factory=aiohttp.ClientSession,
            key='',
            limiter=tfnsw_limiter(),
        ),
    )
