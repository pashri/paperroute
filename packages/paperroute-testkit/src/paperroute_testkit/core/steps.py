"""Stepping helpers for reducer tests."""

from dataclasses import replace
from datetime import time
from typing import cast

from paperroute_device.core.device import on_reset
from paperroute_device.core.device_model import ResetKind
from paperroute_device.core.device_settings import Window
from paperroute_device.core.device_state import KitSaved
from paperroute_device.core.kit_effects import SaveState
from paperroute_device.core.panel_screens import device_view
from paperroute_device.core.panel_views import InfoRow
from paperroute_device.core.sleep import slept as kit_slept
from paperroute_transit.core.messages import (
    Effect,
    Event,
    FeedFailed,
    FetchFeed,
)
from paperroute_transit.core.panel_screens import transit_rows
from paperroute_transit.core.saved import Saved
from paperroute_transit.core.state import device_of, step
from paperroute_transit.core.state_model import State, with_device
from paperroute_transit.core.state_views import saved_of


def wakeful(*, state: State) -> State:
    """
    Set quiet hours that never come.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    State
        State whose My hours start and end together.
    """
    never = Window(start=time(0), end=time(0))
    device = replace(state.settings.device, my_hours=never)
    return replace(state, settings=replace(state.settings, device=device))


def slept(*, state: State, by_quiet: bool = False) -> State:
    """
    Put the screen to sleep as the kit does.

    Parameters
    ----------
    state : State
        State.
    by_quiet : bool
        Whether quiet hours sleep it, so a tick can end the sleep.

    Returns
    -------
    State
        State asleep, the menu closed and no wake running.
    """
    device = kit_slept(device=device_of(state=state), by_quiet=by_quiet)
    return with_device(state=state, device=device)


def reset_asked(
    *, state: State, kind: ResetKind
) -> tuple[State, tuple[Effect, ...]]:
    """
    Start a reset as the kit does once it is confirmed.

    Parameters
    ----------
    state : State
        State.
    kind : ResetKind
        The reset.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        State with the reset under way, and the kit's effects.
    """
    device, effects = on_reset(device=device_of(state=state), kind=kind)
    kept = with_device(state=state, device=device)
    return kept, cast(tuple[Effect, ...], effects)


def device_rows(*, state: State) -> tuple[InfoRow, ...]:
    """
    Gather the Device screen's lines as the kit builds them for transit.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    tuple[InfoRow, ...]
        The kit's lines around transit's.
    """
    view = device_view(
        device=device_of(state=state),
        app_rows=transit_rows(state=state),
        zone=state.city.zone,
    )
    return view.rows


def untouched(*, state: State) -> State:
    """
    Blank the time of the last knob touch.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    State
        State with no touch noted.
    """
    return replace(state, refresh=replace(state.refresh, touched_at=None))


def step_untouched(
    *, state: State, event: Event
) -> tuple[State, tuple[Effect, ...]]:
    """
    Apply an event, blanking the touch it notes.

    Parameters
    ----------
    state : State
        State before, with no touch noted.
    event : Event
        What happened.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        State after, with no touch noted, and effects.
    """
    after, effects = step(state, event)
    return untouched(state=after), effects


def answer_fetches(
    *, state: State, effects: tuple[Effect, ...]
) -> tuple[State, tuple[Effect, ...]]:
    """
    Answer every feed fetched in some effects with a failure.

    A failure stores nothing, so the poll cycle's frame is released
    without changing what it shows.

    Parameters
    ----------
    state : State
        State after the effects were made.
    effects : tuple[Effect, ...]
        Effects, some of them FetchFeeds.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        State after the answers, and the effects followed by theirs.
    """
    fetches = [effect for effect in effects if isinstance(effect, FetchFeed)]
    for fetch in fetches:
        state, more = step(
            state,
            FeedFailed(feed=fetch.feed, at=state.now, reason='test'),
        )
        effects = (*effects, *more)
    return state, effects


def step_answered(
    *, state: State, event: Event
) -> tuple[State, tuple[Effect, ...]]:
    """
    Apply an event, then answer every feed it fetched.

    Parameters
    ----------
    state : State
        State before.
    event : Event
        What happened.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        State after the answers, and every effect in order.
    """
    after, effects = step(state, event)
    return answer_fetches(state=after, effects=effects)


def save_of(*, state: State) -> SaveState:
    """
    Build the save the kit writes for a state.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    SaveState
        The device settings and transit's section, as the kit saves them.
    """
    return SaveState(
        saved=KitSaved(
            device=state.settings.device,
            app=saved_of(state=state),
        ),
    )


def unpacked(*, saved: KitSaved) -> Saved:
    """
    Join the kit's saved parts back into transit's.

    Parameters
    ----------
    saved : KitSaved
        What the kit saves.

    Returns
    -------
    Saved
        Transit's section with the kit's device settings in it.
    """
    app = cast(Saved, saved.app)
    settings = replace(app.settings, device=saved.device)
    return replace(app, settings=settings)


def written(*, save: SaveState) -> Saved:
    """
    Read a save as transit's saved section.

    Parameters
    ----------
    save : SaveState
        The save.

    Returns
    -------
    Saved
        What it writes.
    """
    return unpacked(saved=save.saved)


def last_saved(*, state: State) -> Saved | None:
    """
    Read what the kit last saved for a state.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    Saved | None
        What was last saved, or None before the first save.
    """
    saved = state.kit.saved
    return None if saved is None else unpacked(saved=saved)
