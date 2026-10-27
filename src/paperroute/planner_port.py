"""The Trip Planner port: finds journeys between two stops."""

from typing import Protocol

from paperroute.core.trip_search import Journey, TripSearch


class TripPlanner(Protocol):
    """Finds journeys between two stops."""

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

    async def close(self) -> None:
        """Release any connections."""
