"""The day's other lines a plain pin's map draws."""

from dataclasses import replace
from datetime import date, datetime, tzinfo

from paperroute_transit.core.alert_state import service_date
from paperroute_transit.core.map_fit import (
    line_frame,
    line_framed,
    steady_frame,
)
from paperroute_transit.core.map_lines import LineChoice
from paperroute_transit.core.map_state import DayLines
from paperroute_transit.core.map_view import MapInputs
from paperroute_transit.core.messages import Effect, LoadDayLines, MapData
from paperroute_transit.core.model import Departure, is_connection
from paperroute_transit.core.modes import skippable_modes
from paperroute_transit.core.places import shown
from paperroute_transit.core.state_model import State, may_load
from paperroute_transit.core.stop_view import StopView


def view_day(*, stop_view: StopView, now: datetime, zone: tzinfo) -> date:
    """
    Find the service day the Stop view lists.

    Parameters
    ----------
    stop_view : StopView
        The pin's Stop view.
    now : datetime
        Current time.
    zone : tzinfo
        Local time zone.

    Returns
    -------
    date
        The service day of the first departure listed, else today.
    """
    if stop_view.head_departures:
        return stop_view.head_departures[0].service_day
    return service_date(moment=now, zone=zone)


def day_request(*, state: State, stop_view: StopView) -> LoadDayLines | None:
    """
    Build the load of the day's lines for the shown pin.

    Parameters
    ----------
    state : State
        State.
    stop_view : StopView
        The pin's Stop view.

    Returns
    -------
    LoadDayLines | None
        The load; None for a pin with a Destination or a connection pin.
    """
    pin = shown(state=state)
    if pin.destination is not None or is_connection(pin=pin):
        return None
    return LoadDayLines(
        pin=pin,
        day=view_day(
            stop_view=stop_view, now=state.now, zone=state.city.zone,
        ),
        timetable=state.saving.timetable,
        skip_school=state.settings.board.skip_school_buses,
    )


def request_day_lines(
    *, state: State, stop_view: StopView
) -> tuple[State, tuple[Effect, ...]]:
    """
    Ask for the day's lines when the held ones are not for this pin and day.

    Parameters
    ----------
    state : State
        State.
    stop_view : StopView
        The pin's Stop view.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        State with the load pending, and the load; unchanged when the
        request is the held one, there is none, or a failure is backing
        off.
    """
    request = day_request(state=state, stop_view=stop_view)
    held = state.map.day_lines
    if (
        request is None
        or (held is not None and held.request == request)
        or not may_load(state=state, kind=MapData.LINES)
    ):
        return state, ()
    pending = replace(state.map, day_lines=DayLines(request=request))
    return replace(state, map=pending), (request,)


def day_extras(
    *, state: State, stop_view: StopView
) -> tuple[Departure, ...]:
    """
    List the day's other lines the map draws.

    Parameters
    ----------
    state : State
        State.
    stop_view : StopView
        The pin's Stop view.

    Returns
    -------
    tuple[Departure, ...]
        The held departures when they answer the current request, else
        none.
    """
    held = state.map.day_lines
    if (
        held is None
        or held.departures is None
        or held.request != day_request(state=state, stop_view=stop_view)
    ):
        return ()
    return held.departures


def line_choice(*, state: State, stop_view: StopView) -> LineChoice:
    """
    Choose what a plain pin's map draws besides its own departures.

    Parameters
    ----------
    state : State
        State.
    stop_view : StopView
        The pin's Stop view.

    Returns
    -------
    LineChoice
        The From's station, the day's other lines, and the kinds the
        school buses setting hides.
    """
    hidden = (
        skippable_modes(modes=state.city.modes)
        if state.settings.board.skip_school_buses
        else frozenset()
    )
    return LineChoice(
        station=state.board.station or shown(state=state).origin.stop_id,
        extras=day_extras(state=state, stop_view=stop_view),
        hidden=hidden,
    )


def plain_frame(
    *, state: State, stop_view: StopView, inputs: MapInputs,
) -> MapInputs:
    """
    Frame a plain pin's map by its kind, then the map framing setting.

    Parameters
    ----------
    state : State
        State.
    stop_view : StopView
        The pin's Stop view.
    inputs : MapInputs
        The map's inputs, centred on the From.

    Returns
    -------
    MapInputs
        `line_frame` for a kind chosen by line, whatever the setting;
        else `steady_frame` with the lines the pin draws.
    """
    if line_framed(kind=shown(state=state).kind, modes=state.city.modes):
        return line_frame(inputs=inputs)
    return steady_frame(
        inputs=inputs,
        framing=state.settings.board.map_framing,
        held=state.map.frame,
        key=state.map.frame_key,
        choice=line_choice(state=state, stop_view=stop_view),
    )
