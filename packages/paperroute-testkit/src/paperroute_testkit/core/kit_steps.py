"""Step transit's state through the device kit, as the kit's loop does."""

from collections.abc import Sequence
from dataclasses import fields, replace
from importlib.resources import files
from typing import TYPE_CHECKING, Final, cast

from paperroute_device.assets import AssetRoot
from paperroute_device.core.device_model import DeviceKey
from paperroute_device.core.device_state import (
    ClockWait,
    DeviceState,
    KitSaved,
)
from paperroute_device.core.kit_events import DeviceChange, SettingsPosted
from paperroute_device.core.kit_state import KitState, with_app
from paperroute_device.core.reducer import step as kit_reducer
from paperroute_transit.city_parts import CityParts
from paperroute_transit.core.messages import Effect, Event, SettingsChanged
from paperroute_transit.core.panel_views import SleepView
from paperroute_transit.core.state_model import State, with_device
from paperroute_transit.core.state_views import saved_of
from paperroute_transit.transit_app import TransitApp

if TYPE_CHECKING:
    from paperroute_transit.ports import FeedsClient


class NoFeeds:
    """A feeds client that has no feeds."""

    async def fetch(self, *, feed: str) -> bytes:
        """
        Fetch nothing.

        Parameters
        ----------
        feed : str
            The feed asked for.

        Raises
        ------
        LookupError
            Always.
        """
        raise LookupError(f'no feed {feed}')

    async def close(self) -> None:
        """Close nothing."""


CARRIED_KEPT: Final[int] = 64
CARRIED: Final[dict[int, KitState[State]]] = {}


def no_feeds() -> FeedsClient:
    """
    Make a client with no feeds.

    Returns
    -------
    FeedsClient
        The client.
    """
    return NoFeeds()


def no_keys() -> Sequence[DeviceKey]:
    """
    Give no keys.

    Returns
    -------
    Sequence[DeviceKey]
        None at all.
    """
    return ()


def no_ways(*, live: bool) -> None:
    """
    Give no suggestion plug.

    Parameters
    ----------
    live : bool
        Ignored.
    """
    del live


def no_status(*, status: object) -> str:
    """
    Write no status line.

    Parameters
    ----------
    status : object
        Ignored.

    Returns
    -------
    str
        An empty line.
    """
    del status
    return ''


QUIET_PARTS: Final = CityParts(
    assets=AssetRoot(web=files('paperroute_transit')),
    live_feeds=no_feeds,
    keys=no_keys,
    nav=(),
    hint='',
    suggest=no_ways,
    status_line=no_status,
    sheets=(),
)


def device_of(*, state: State) -> DeviceState:
    """
    Build the kit's device state from the state's mirror of it.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    DeviceState
        The device state; the clock was never set after a wait, and the
        start is the refresh's start.
    """
    return DeviceState(
        now=state.now,
        panel=state.panel,
        settings=state.settings.device,
        power=state.power,
        clock=ClockWait(
            wait_until=state.clock_wait_until,
            gave_up=state.clock_gave_up,
            known=state.clock_known,
        ),
        last_view=state.last_view,
        last_was_sleep=isinstance(state.last_view, SleepView),
        in_menu=state.refresh.in_menu,
        started_at=state.refresh.started_at,
        touched_at=state.refresh.touched_at,
    )


def kit_of(*, state: State, stored: bool = True) -> KitState[State]:
    """
    Wrap a state in the kit's state, as if it was just started.

    Parameters
    ----------
    state : State
        State.
    stored : bool
        Whether the state came from the file, so what it keeps is
        already written.

    Returns
    -------
    KitState[State]
        Kit state holding `state`.
    """
    saved = KitSaved(device=state.settings.device, app=saved_of(state=state))
    return KitState(
        device=device_of(state=state),
        app=state,
        running=state.running,
        saved=saved if stored else None,
        stored=stored,
        written_at=state.now,
    )


def posted(*, event: Event) -> object:
    """
    Post a board settings change as the Settings page does.

    Parameters
    ----------
    event : object
        What happened.

    Returns
    -------
    object
        A SettingsChanged inside a SettingsPosted with no device change;
        any other event as it is.
    """
    if isinstance(event, SettingsChanged):
        return SettingsPosted(device=DeviceChange(), app=event)
    return event


DEVICE_FIELDS: Final[frozenset[str]] = frozenset(
    item.name for item in fields(DeviceChange)
)


def settings_posted(**given: object) -> SettingsPosted:
    """
    Post settings as the Settings page does, split between the parts.

    Parameters
    ----------
    **given : object
        Changed settings, device and board alike.

    Returns
    -------
    SettingsPosted
        The device fields as the kit's change and the rest, if any, as
        the board's.
    """
    device = {
        name: value
        for name, value in given.items()
        if name in DEVICE_FIELDS
    }
    board = {
        name: value
        for name, value in given.items()
        if name not in DEVICE_FIELDS
    }
    return SettingsPosted(
        device=DeviceChange(**device),  # type: ignore[arg-type]
        app=SettingsChanged(**board) if board else None,  # type: ignore[arg-type]
    )


def kit_step(
    *, kit: KitState[State], event: object,
) -> tuple[KitState[State], tuple[Effect, ...]]:
    """
    Apply one event through the kit's reducer with transit's app.

    Parameters
    ----------
    kit : KitState[State]
        Kit state.
    event : object
        What happened.

    Returns
    -------
    tuple[KitState[State], tuple[Effect, ...]]
        The kit state after the event, and the effects.
    """
    app = TransitApp(city=kit.app.city, parts=QUIET_PARTS)
    after, effects = kit_reducer(
        kit=kit,
        app=app,
        zone=kit.app.city.zone,
        event=posted(event=cast(Event, event)),
    )
    return after, cast(tuple[Effect, ...], effects)


def app_of(*, kit: KitState[State]) -> State:
    """
    Read transit's state from the kit's.

    Parameters
    ----------
    kit : KitState[State]
        Kit state.

    Returns
    -------
    State
        Transit's state with the kit's device state in its mirror.
    """
    state = with_device(state=kit.app, device=kit.device)
    if state.running == kit.running:
        return state
    return replace(state, running=kit.running)


def step(state: State, event: object) -> tuple[State, tuple[Effect, ...]]:
    """
    Apply one event, carrying the kit's state between chained calls.

    A state returned by an earlier `step` keeps that step's kit state:
    what was last written, when, and any change still waiting to be
    saved. Any other state, including one changed with `replace` since,
    starts as if it was just written, with nothing waiting to be saved.
    Thread `kit_of` and `kit_step` to keep the kit state through edits.

    Parameters
    ----------
    state : State
        Current state, its device fields a mirror of the kit's.
    event : object
        What happened.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        Next state and the effects.
    """
    carried = CARRIED.get(id(state))
    kit = (
        carried
        if carried is not None and carried.app is state
        else kit_of(state=state)
    )
    after, effects = kit_step(kit=kit, event=event)
    result = app_of(kit=after)
    CARRIED[id(result)] = with_app(kit=after, app=result)
    while len(CARRIED) > CARRIED_KEPT:
        del CARRIED[next(iter(CARRIED))]
    return result, effects
