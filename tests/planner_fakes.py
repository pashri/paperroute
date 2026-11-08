"""A hand-written stand-in for the Trip Planner."""

from dataclasses import dataclass, field

from paperroute.core.trip_search import (
    Journey,
    PlannerBusy,
    PlannerUnavailable,
    TripSearch,
)


@dataclass
class FakeTripPlanner:
    """
    A Trip Planner that gives fixed journeys, or fails.

    Attributes
    ----------
    journeys : tuple[Journey, ...]
        What `trips` gives.
    down : bool
        True to raise `PlannerUnavailable`.
    asked : list[tuple[str, str, TripSearch]]
        The calls made.
    closed : bool
        True once closed.
    busy : bool
        True to raise `PlannerBusy`.
    """

    journeys: tuple[Journey, ...] = ()
    down: bool = False
    asked: list[tuple[str, str, TripSearch]] = field(default_factory=list)
    closed: bool = False
    busy: bool = False

    async def close(self) -> None:
        """Note that it was closed."""
        self.closed = True

    async def trips(
        self, *, origin_id: str, destination_id: str, search: TripSearch,
    ) -> tuple[Journey, ...]:
        """
        Give the fixed journeys.

        Parameters
        ----------
        origin_id : str
            Recorded.
        destination_id : str
            Recorded.
        search : TripSearch
            Recorded.

        Returns
        -------
        tuple[Journey, ...]
            The journeys.

        Raises
        ------
        PlannerBusy
            When `busy` is set.
        PlannerUnavailable
            When `down` is set.
        """
        self.asked.append((origin_id, destination_id, search))
        if self.busy:
            raise PlannerBusy('busy')
        if self.down:
            raise PlannerUnavailable('down')
        return self.journeys
