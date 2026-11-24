"""Shared helpers for the . tests: invariant drivers."""

from dataclasses import dataclass
from datetime import datetime

from paperroute_device.core.kit_events import Tick
from paperroute_testkit.core.steps import step_answered
from paperroute_transit.core.messages import DeparturesLoaded, LoadDepartures
from paperroute_transit.core.model import Departure
from paperroute_transit.core.state_model import State


@dataclass
class WindowedTimetable:
    """A timetable that answers a load with what falls in its window."""

    departures: tuple[Departure, ...]

    def load(self, *, effect: LoadDepartures) -> DeparturesLoaded:
        """
        Answer a load request.

        Parameters
        ----------
        effect : LoadDepartures
            The request.

        Returns
        -------
        DeparturesLoaded
            Departures from start to end, as the repository returns them.
        """
        found = tuple(
            dep
            for dep in self.departures
            if effect.start.timestamp()
            <= dep.departs.timestamp()
            < effect.end.timestamp()
        )
        return DeparturesLoaded(
            pin=effect.pin,
            departures=found,
            until=effect.end,
            timetable=effect.timetable,
        )


def run_tick(
    *, state: State, timetable: WindowedTimetable, now: datetime
) -> State:
    """
    Tick, then answer any load request at once, and the feeds with
    failures.

    Parameters
    ----------
    state : State
        State before.
    timetable : WindowedTimetable
        Where loads are answered.
    now : datetime
        Tick time.

    Returns
    -------
    State
        State after the tick and the load.
    """
    state, effects = step_answered(state=state, event=Tick(now=now))
    for effect in effects:
        if isinstance(effect, LoadDepartures):
            state, _ = step_answered(
                state=state, event=timetable.load(effect=effect),
            )
    return state
