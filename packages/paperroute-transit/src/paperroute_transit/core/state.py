"""Step transit's state through the device kit's reducer."""

from collections.abc import Sequence
from dataclasses import fields, replace
from importlib.resources import files
from typing import TYPE_CHECKING, Final, cast

from paperroute_device.assets import AssetRoot
from paperroute_device.core.device_model import DeviceKey
from paperroute_device.core.device_state import ClockWait, DeviceState
from paperroute_device.core.kit_events import DeviceChange, SettingsPosted
from paperroute_device.core.kit_state import KitState
from paperroute_device.core.reducer import step as kit_step
from paperroute_transit.city_parts import CityParts
from paperroute_transit.core.messages import Effect, Event, SettingsChanged
from paperroute_transit.core.state_model import KitMirror, State, with_device
from paperroute_transit.transit_app import TransitApp

if TYPE_CHECKING:
    from paperroute_transit.ports import FeedsClient

DEVICE_FIELDS: Final[frozenset[str]] = frozenset(
    item.name for item in fields(DeviceChange)
)


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


REDUCER_PARTS: Final = CityParts(
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
        The device state, with the kit's own fields from `state.kit`.
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
            set_at=state.kit.set_at,
        ),
        last_view=state.last_view,
        last_was_sleep=state.kit.last_was_sleep,
        in_menu=state.refresh.in_menu,
        started_at=state.kit.started_at,
        touched_at=state.refresh.touched_at,
    )


def posted(*, event: Event) -> object:
    """
    Split a Settings page change into the kit's part and transit's.

    Parameters
    ----------
    event : Event
        What happened.

    Returns
    -------
    object
        A SettingsChanged as a SettingsPosted, its device fields in the
        kit's part and the rest, if any, in transit's; any other event
        as it is.
    """
    if not isinstance(event, SettingsChanged):
        return event
    given = {
        item.name: getattr(event, item.name)
        for item in fields(event)
        if getattr(event, item.name) is not None
    }
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
        device=DeviceChange(**device),
        app=SettingsChanged(**board) if board else None,
    )


def step(state: State, event: Event) -> tuple[State, tuple[Effect, ...]]:
    """
    Apply one event through the kit's reducer with transit's app.

    Parameters
    ----------
    state : State
        Current state, its device fields a mirror of the kit's.
    event : Event
        What happened.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        Next state, with the kit's device state written back to the
        mirror and the kit's own fields to `state.kit`, and the effects.
    """
    mirror = state.kit
    kit = KitState(
        device=device_of(state=state),
        app=state,
        running=state.running,
        changed_at=mirror.changed_at,
        saved=mirror.saved,
        stored=mirror.stored,
        written_at=mirror.written_at,
    )
    after, effects = kit_step(
        kit=kit,
        app=TransitApp(city=state.city, parts=REDUCER_PARTS),
        zone=state.city.zone,
        event=posted(event=event),
    )
    return kept_of(state=state, after=after), cast(tuple[Effect, ...], effects)


def kept_of(*, state: State, after: KitState[State]) -> State:
    """
    Write the kit's state back into transit's.

    Parameters
    ----------
    state : State
        State before the event.
    after : KitState[State]
        The kit's state after it.

    Returns
    -------
    State
        Transit's state with the kit's device state in the mirror and
        its own fields in `state.kit`; `state` itself when nothing moved.
    """
    device = after.device
    mirror = KitMirror(
        changed_at=after.changed_at,
        saved=after.saved,
        stored=after.stored,
        last_was_sleep=device.last_was_sleep,
        started_at=device.started_at,
        in_menu=device.in_menu,
        set_at=device.clock.set_at,
        written_at=after.written_at,
    )
    kept = with_device(state=after.app, device=device)
    unmoved = (
        kept is state
        and mirror == state.kit
        and after.running == state.running
    )
    if unmoved:
        return state
    return replace(kept, running=after.running, kit=mirror)
