"""Transit's settings, status and the guards over its effects."""

from dataclasses import fields, replace

from paperroute_transit.core.alert_state import alert_clocks_cleared
from paperroute_transit.core.banners import feeds_held
from paperroute_transit.core.messages import (
    Effect,
    FetchFeed,
    RebuildTimetable,
    SettingsChanged,
)
from paperroute_transit.core.quota import calls_today
from paperroute_transit.core.saved import Settings, with_changes
from paperroute_transit.core.state_model import Cycle, State
from paperroute_transit.core.state_views import settled


def school_changed(*, before: Settings, after: Settings) -> bool:
    """
    Say whether a settings change alters which school routes are shown.

    Parameters
    ----------
    before : Settings
        Settings before the change.
    after : Settings
        Settings after it.

    Returns
    -------
    bool
        True when either school switch differs.
    """
    return (
        before.board.skip_school_buses != after.board.skip_school_buses
        or before.board.skip_school_on_route_pins
        != after.board.skip_school_on_route_pins
    )


def on_settings(
    *, state: State, event: SettingsChanged
) -> tuple[State, tuple[Effect, ...]]:
    """
    Store the board settings given; the next tick saves them.

    A change to the school switch clears the board, so the next tick
    reads the departures again.

    Parameters
    ----------
    state : State
        State.
    event : SettingsChanged
        The new board settings; a None field is left as it is.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        State with the settings stored, settled.
    """
    given = {
        item.name: getattr(event, item.name)
        for item in fields(event)
        if getattr(event, item.name) is not None
    }
    settings = with_changes(settings=state.settings, changes=given)
    after = replace(state, settings=settings)
    if school_changed(before=state.settings, after=settings):
        after = replace(
            after,
            board=replace(
                after.board,
                departures=(),
                loaded_until=None,
                loading=False,
            ),
        )
    return settled(state=after)


def power_guard(
    *, state: State, result: tuple[State, tuple[Effect, ...]]
) -> tuple[State, tuple[Effect, ...]]:
    """
    Drop every RebuildTimetable while a reset is under way.

    The kit drops its own redraws and saves; this keeps a rebuild from
    writing the files the reset deletes.

    Parameters
    ----------
    state : State
        State before the event, its device fields a mirror of the kit's.
    result : tuple[State, tuple[Effect, ...]]
        Transit's state and effects.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        The result, without RebuildTimetables while a reset is under way.
    """
    after, effects = result
    if state.panel.resetting is None:
        return result
    kept = tuple(
        effect
        for effect in effects
        if not isinstance(effect, RebuildTimetable)
    )
    return after, kept


def quiet_guard(
    *, result: tuple[State, tuple[Effect, ...]]
) -> tuple[State, tuple[Effect, ...]]:
    """
    Drop every FetchFeed from a result while the feeds are held.

    Parameters
    ----------
    result : tuple[State, tuple[Effect, ...]]
        The handler's state and effects.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        The result, without FetchFeeds when `feeds_held` says so for the
        state after the event. Held feeds also have their poll clocks
        and cycle cleared, so a cycle starts at once when they are let
        go.
    """
    after, effects = result
    if not feeds_held(state=after):
        return result
    kept = tuple(
        effect for effect in effects if not isinstance(effect, FetchFeed)
    )
    cleared = alert_clocks_cleared(
        state=replace(
            after,
            last_fetch=None,
            map=replace(after.map, fetched_at={}),
            cycle=Cycle(free=after.cycle.free),
        ),
    )
    return cleared, kept


def app_status(*, state: State) -> dict[str, object]:
    """
    Gather what the app reports about itself.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    dict[str, object]
        The timetable, its schema, whether a rebuild runs or the last
        one failed, and the calls made today, in that order.
    """
    return {
        'timetable': state.saving.timetable,
        'schema': state.rebuild.schema,
        'rebuilding': state.rebuild.running,
        'rebuild_failed': state.rebuild.failed,
        'calls_today': calls_today(quota=state.quota, day=state.now.date()),
    }
