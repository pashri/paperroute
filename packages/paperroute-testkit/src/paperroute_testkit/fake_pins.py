"""A hand-written stand-in for the pin editor's timetable choices."""

from collections.abc import Collection
from dataclasses import dataclass, field

from paperroute.city import SydneyMode
from paperroute.core.chooser import Choice
from paperroute.core.pin_choices import KIND_NAMES, ChangeStop, StopMatch
from paperroute_transit.core.model import StopPlace
from paperroute_transit.core.route_colours import RouteColour


@dataclass
class FakePinOptions:
    """
    Pin options that give fixed answers.

    Attributes
    ----------
    places : dict[str, StopPlace]
        The places that exist, by ID.
    lines : dict[tuple[str, SydneyMode], tuple[Choice, ...]]
        The lines at a stop, by stop ID and kind.
    to : dict[tuple[str, str], tuple[StopPlace, ...]]
        The To places, by stop ID and line key.
    only : dict[tuple[str, SydneyMode], tuple[str, ...]]
        The route filter, by stop ID and kind.
    near : tuple[StopPlace, ...]
        What `nearby` gives.
    found : tuple[StopMatch, ...]
        What `search` gives.
    colours : dict[str, RouteColour]
        Route colours by short name.
    changes : tuple[ChangeStop, ...]
        What `change_stops` gives.
    reached : tuple[StopPlace, ...]
        What `reached_from` gives for a place not in `reached_by`.
    reached_by : dict[str, tuple[StopPlace, ...]]
        What `reached_from` gives, by stop ID.
    between : tuple[str, ...]
        What `routes_between` gives.
    parents : dict[str, str]
        Station IDs by Trip Planner stop ID, for `trip_stop`.
    """

    places: dict[str, StopPlace] = field(default_factory=dict)
    lines: dict[tuple[str, SydneyMode], tuple[Choice, ...]] = field(
        default_factory=dict,
    )
    to: dict[tuple[str, str], tuple[StopPlace, ...]] = field(
        default_factory=dict,
    )
    only: dict[tuple[str, SydneyMode], tuple[str, ...]] = field(
        default_factory=dict,
    )
    near: tuple[StopPlace, ...] = ()
    found: tuple[StopMatch, ...] = ()
    colours: dict[str, RouteColour] = field(default_factory=dict)
    changes: tuple[ChangeStop, ...] = ()
    reached: tuple[StopPlace, ...] = ()
    reached_by: dict[str, tuple[StopPlace, ...]] = field(
        default_factory=dict,
    )
    between: tuple[str, ...] = ()
    parents: dict[str, str] = field(default_factory=dict)
    skipped: list[bool] = field(default_factory=list)

    def search(
        self, *, text: str, skip_school: bool = False,
    ) -> tuple[StopMatch, ...]:
        """
        Give the fixed matches.

        Parameters
        ----------
        text : str
            Ignored.
        skip_school : bool
            Noted in `skipped`.

        Returns
        -------
        tuple[StopMatch, ...]
            The matches.
        """
        self.skipped.append(skip_school)
        return self.found

    def place(self, *, stop_id: str) -> StopPlace | None:
        """
        Give a known place.

        Parameters
        ----------
        stop_id : str
            Stop ID.

        Returns
        -------
        StopPlace | None
            The place, or None when it is not known.
        """
        return self.places.get(stop_id)

    def trip_stop(self, *, stop_id: str) -> StopPlace | None:
        """
        Give a known place, lifted to its parent.

        Parameters
        ----------
        stop_id : str
            Trip Planner stop ID.

        Returns
        -------
        StopPlace | None
            The place, or None when it is not known.
        """
        return self.places.get(self.parents.get(stop_id, stop_id))

    def kinds_at(self, *, stop_id: str) -> tuple[SydneyMode, ...]:
        """
        Give the kinds that have lines at a stop.

        Parameters
        ----------
        stop_id : str
            Stop ID.

        Returns
        -------
        tuple[SydneyMode, ...]
            Kinds in display order.
        """
        return tuple(
            kind for kind in KIND_NAMES if self.lines.get((stop_id, kind))
        )

    def lines_at(self, *, stop_id: str, kind: SydneyMode) -> tuple[Choice, ...]:
        """
        Give the fixed lines of a stop and kind.

        Parameters
        ----------
        stop_id : str
            Stop ID.
        kind : SydneyMode
            Kind of route.

        Returns
        -------
        tuple[Choice, ...]
            The lines.
        """
        return self.lines.get((stop_id, kind), ())

    def destinations(
        self, *, origin: StopPlace, kind: SydneyMode, line: Choice,
    ) -> tuple[StopPlace, ...]:
        """
        Give the fixed To places of a stop and line.

        Parameters
        ----------
        origin : StopPlace
            The From.
        kind : SydneyMode
            Ignored.
        line : Choice
            The line.

        Returns
        -------
        tuple[StopPlace, ...]
            The places.
        """
        return self.to.get((origin.stop_id, line.key), ())

    def filters_at(
        self, *, stop_id: str, kind: SydneyMode,
    ) -> tuple[str, ...]:
        """
        Give the fixed route filter of a stop and kind.

        Parameters
        ----------
        stop_id : str
            Stop ID.
        kind : SydneyMode
            Kind of route.

        Returns
        -------
        tuple[str, ...]
            The route numbers.
        """
        return self.only.get((stop_id, kind), ())

    def nearby(
        self, *, place: StopPlace, kind: str | None,
    ) -> tuple[StopPlace, ...]:
        """
        Give the fixed nearby stops.

        Parameters
        ----------
        place : StopPlace
            Ignored.
        kind : str | None
            Ignored.

        Returns
        -------
        tuple[StopPlace, ...]
            The stops.
        """
        return self.near

    def change_stops(self, *, place: StopPlace) -> tuple[ChangeStop, ...]:
        """
        Give the fixed change stops.

        Parameters
        ----------
        place : StopPlace
            Ignored.

        Returns
        -------
        tuple[ChangeStop, ...]
            The change stops.
        """
        return self.changes

    def reached_from(self, *, place: StopPlace) -> tuple[StopPlace, ...]:
        """
        Give the fixed places reached.

        Parameters
        ----------
        place : StopPlace
            Where the ride starts.

        Returns
        -------
        tuple[StopPlace, ...]
            The places for its stop ID, else `reached`.
        """
        return self.reached_by.get(place.stop_id, self.reached)

    def routes_between(
        self, *, origin: StopPlace, destination: StopPlace,
    ) -> tuple[str, ...]:
        """
        Give the fixed routes between two places.

        Parameters
        ----------
        origin : StopPlace
            Ignored.
        destination : StopPlace
            Ignored.

        Returns
        -------
        tuple[str, ...]
            The routes.
        """
        return self.between

    def route_colours(
        self, *, names: Collection[str],
    ) -> dict[str, RouteColour]:
        """
        Give the fixed colours of the names asked for.

        Parameters
        ----------
        names : Collection[str]
            Route short names.

        Returns
        -------
        dict[str, RouteColour]
            The colours of those names that have one.
        """
        return {
            name: colour
            for name, colour in self.colours.items()
            if name in names
        }
