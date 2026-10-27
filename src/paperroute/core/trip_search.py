"""What a Trip Planner search asks for and what a journey is."""

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Final

ARRIVE_BY: Final[time] = time(9, 0)
SATURDAY: Final[int] = 5


class PlannerUnavailable(Exception):
    """The Trip Planner didn't give an answer."""


class PlannerBusy(PlannerUnavailable):
    """The Trip Planner refused the request as too many (HTTP 429)."""


@dataclass(frozen=True)
class TripSearch:
    """
    When to travel.

    Attributes
    ----------
    when : datetime
        The time to leave at or arrive by.
    arrive : bool
        True to arrive by `when`; False to leave at `when`.
    """

    when: datetime
    arrive: bool


@dataclass(frozen=True)
class JourneyLeg:
    """
    One ride of a journey.

    Attributes
    ----------
    origin_id : str
        Trip Planner ID of the stop where it is boarded.
    destination_id : str
        Trip Planner ID of the stop where it is left.
    route : str
        The route's short name, such as `T1` or `606`.
    departs : datetime
        Planned departure from the origin.
    arrives : datetime
        Planned arrival at the destination.
    """

    origin_id: str
    destination_id: str
    route: str
    departs: datetime
    arrives: datetime


@dataclass(frozen=True)
class Journey:
    """
    A way to get there by transit, walking legs left out.

    Attributes
    ----------
    legs : tuple[JourneyLeg, ...]
        The rides in order.
    """

    legs: tuple[JourneyLeg, ...]

    @property
    def changes(self) -> int:
        """
        Count the changes.

        Returns
        -------
        int
            One fewer than the number of rides.
        """
        return len(self.legs) - 1


def default_search(*, now: datetime) -> TripSearch:
    """
    Pick the default search: arrive by 9:00 on a weekday.

    Parameters
    ----------
    now : datetime
        The current time.

    Returns
    -------
    TripSearch
        Arrive by 9:00 today when today is a weekday and it is before
        9:00; otherwise by 9:00 on the next weekday.
    """
    day = now.date()
    if day.weekday() >= SATURDAY or now.time() >= ARRIVE_BY:
        day += timedelta(days=1)
    while day.weekday() >= SATURDAY:
        day += timedelta(days=1)
    return TripSearch(
        when=datetime.combine(day, ARRIVE_BY, tzinfo=now.tzinfo),
        arrive=True,
    )
