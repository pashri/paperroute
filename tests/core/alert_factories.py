"""Alerts built from real feed text, for matching tests."""

from datetime import datetime
from typing import Final

from paperroute_testkit.city_setting import city_under_test
from paperroute_transit.core.alert_model import Alert, Informed, Period
from paperroute_transit.core.model import Pin, StopPlace


def sydney(
    *, month: int = 10, day: int, hour: int, minute: int = 0,
    second: int = 0,
) -> datetime:
    """
    Build a Sydney time in 2026.

    Parameters
    ----------
    month : int
        Month.
    day : int
        Day.
    hour : int
        Hour.
    minute : int
        Minute.
    second : int
        Second.

    Returns
    -------
    datetime
        The time.
    """
    return datetime(
        year=2026,
        month=month,
        day=day,
        hour=hour,
        minute=minute,
        second=second,
        tzinfo=city_under_test().zone,
    )


def alert(
    *,
    alert_id: str,
    header: str,
    description: str = '',
    periods: tuple[Period, ...] = (),
    route: str | None = None,
    stops: frozenset[str] = frozenset(),
    trip_id: str | None = None,
) -> Alert:
    """
    An alert about one resolved route, stop family or trip.

    Parameters
    ----------
    alert_id : str
        Alert ID.
    header : str
        Header text.
    description : str
        Description text.
    periods : tuple[Period, ...]
        Active windows.
    route : str | None
        Resolved route short name.
    stops : frozenset[str]
        Stop family.
    trip_id : str | None
        Trip ID.

    Returns
    -------
    Alert
        The alert.
    """
    return Alert(
        alert_id=alert_id,
        header=header,
        description=description,
        periods=periods,
        informed=(
            Informed(
                stop_id=min(stops) if stops else None,
                trip_id=trip_id,
                route=route,
                stops=stops,
            ),
        ),
    )


def station_pin(
    *,
    origin: str,
    origin_name: str,
    destination: str | None = None,
    destination_name: str = '',
    kind: str | None = 'train',
) -> Pin:
    """
    A pin between named places.

    Parameters
    ----------
    origin : str
        From stop ID.
    origin_name : str
        From name.
    destination : str | None
        To stop ID, or None.
    destination_name : str
        To name.
    kind : str | None
        Kind of route.

    Returns
    -------
    Pin
        The pin.
    """
    return Pin(
        origin=StopPlace(stop_id=origin, name=origin_name, point=None),
        destination=(
            None
            if destination is None
            else StopPlace(
                stop_id=destination,
                name=destination_name,
                point=None,
            )
        ),
        kind=kind,
    )


WESTERN: Final[Alert] = alert(
    alert_id='5ef3cb29-30b3-5e60-bd7a-343519598275',
    header=(
        'T1 Western Line: Trains run to a changed timetable and stopping '
        'pattern. Buses replace some trains'
    ),
    description=(
        'Saturday 24 and Sunday 25 October\n'
        'Trains run to a changed timetable and stopping pattern between:\n'
        '- Richmond and Liverpool. You may need to change trains at '
        'Parramatta to continue your journey.\n'
        '- Penrith and Central, platforms 1-14 only, making extra stops '
        'at Homebush, Flemington and Burwood.\n'
        'Saturday from 11:40PM to 5:30AM Sunday, buses replace trains '
        'between Olympic Park and Central.\n'
        'Trains run to a changed timetable between Penrith and Olympic '
        'Park.\n'
        'Replacement buses do not leave from Lidcombe.\n'
        'City Circle trackwork may also affect how you travel\n'
        'Trains do not run between Central and Wynyard, or on the City '
        'Circle.\n'
        'For travel in the City use T4 Eastern Suburbs & Illawarra Line '
        'trains, metro, light rail, local buses or walk.\n'
        'T1 North Shore Line trains start from Wynyard.'
    ),
    periods=(
        Period(
            start=sydney(day=24, hour=2),
            end=sydney(day=26, hour=2, second=59),
        ),
    ),
    route='T1',
)
PENRITH_CENTRAL: Final[Alert] = alert(
    alert_id='550a1f97-0e0c-5ea5-bc74-2f2e17e494f7',
    header=(
        'T1 Western Line: Trains run between Penrith and Central, '
        'platforms 1-14'
    ),
    description=(
        'Monday 26 and Tuesday 27 October\n'
        'Nightly from 9:50PM to 1:30AM, trains run between Penrith and '
        'Central, platforms 1-14 only, due to T1 North Shore Line '
        'trackwork.\n'
        'If travelling to T1 North Shore Line stations use metro services '
        'to Victoria Cross, Crows Nest or Chatswood then change for '
        'replacement buses.'
    ),
    periods=(
        Period(
            start=sydney(day=26, hour=21, minute=50),
            end=sydney(day=27, hour=1, minute=30),
        ),
        Period(
            start=sydney(day=27, hour=21, minute=50),
            end=sydney(day=28, hour=1, minute=30),
        ),
    ),
    route='T1',
)
STRATHFIELD_UPDATE: Final[Alert] = alert(
    alert_id='2711aca3-8f6e-5edd-8af3-bf47829b957d',
    header='Station Update - Strathfield',
    periods=(
        Period(start=sydney(day=23, hour=13, minute=5, second=11)),
    ),
    stops=frozenset({'213510'}),
)
XPLORER: Final[Alert] = alert(
    alert_id='xplorer',
    header=(
        'Saturday 24th October 2026 - 631 Sydney to Canberra Xplorer '
        'service.'
    ),
    periods=(
        Period(
            start=sydney(day=24, hour=6),
            end=sydney(day=24, hour=12, minute=15, second=3),
        ),
    ),
    stops=frozenset({'200060'}),
)
RHODES: Final[Alert] = alert(
    alert_id='rhodes',
    header='Rhodes diversions during Fun Run',
    periods=(
        Period(
            start=sydney(day=25, hour=5),
            end=sydney(day=25, hour=11, second=47),
        ),
    ),
    route='526',
)
ROSE_BAY_CLOSURE: Final[Alert] = alert(
    alert_id='30ef7969-2a08-5030-a3f6-624603862126',
    header='Rose Bay Wharf temporary closure',
    periods=(
        Period(
            start=sydney(month=8, day=31, hour=0, minute=1),
            end=sydney(month=12, day=4, hour=23, minute=59, second=4),
        ),
    ),
    route='F9',
    stops=frozenset({'202972'}),
)
ROSE_BAY_PEAK: Final[Alert] = alert(
    alert_id='f689e4d4-e56d-59a3-8be7-d6ff16283a41',
    header='Rose Bay Wharf maintenance',
    periods=(
        Period(
            start=sydney(month=9, day=17, hour=0, minute=1),
            end=sydney(month=12, day=4, hour=23, minute=59, second=26),
        ),
    ),
    route='F9',
)
F9_ONGOING: Final[Alert] = alert(
    alert_id='84aad6bc-4dd5-56d0-9989-3104c0539273',
    header='Reduced weekday and no weekend F9 services at Rose Bay Wharf',
    periods=(Period(start=sydney(month=9, day=18, hour=16, minute=12)),),
    route='F9',
)
ABBOTSFORD: Final[Alert] = alert(
    alert_id='9bae874d-8f1e-538a-85b5-3f8dc60fc20a',
    header='Abbotsford Wharf temporary closure',
    periods=(
        Period(
            start=sydney(day=22, hour=0, minute=1),
            end=sydney(month=12, day=18, hour=23, minute=59, second=12),
        ),
    ),
    route='F3',
)
ROSE_BAY_PIN: Final[Pin] = station_pin(
    origin='202972', origin_name='Rose Bay Wharf', kind='ferry',
)
