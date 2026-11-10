"""Transit's reducers behind the device kit's App contract."""

from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime
from typing import TYPE_CHECKING, Final, cast

from paperroute_device.core.app import App
from paperroute_device.core.device_model import AppRow
from paperroute_device.core.device_state import Banner, DeviceState
from paperroute_device.core.kit_events import Tick
from paperroute_device.core.kit_state import Step
from paperroute_device.core.panel_views import InfoRow
from paperroute_device.ports import Frame
from paperroute_hardware.core.knob_events import KnobEvent
from paperroute_transit.app_pins import BoardObserver, report_pins
from paperroute_transit.core.auto_model import holds_awake
from paperroute_transit.core.banners import OFFLINE_PHRASE
from paperroute_transit.core.city import City
from paperroute_transit.core.device import app_status, power_guard, quiet_guard
from paperroute_transit.core.messages import Effect, Event, ScreenView
from paperroute_transit.core.panel_screens import (
    RESET_HINT,
    TRANSIT_CONFIRM_COPY,
    transit_rows,
)
from paperroute_transit.core.quiet import between_spans
from paperroute_transit.core.quota import (
    QUOTA_SAVE_EVERY,
    quota_only,
    with_calls,
    without_skipped,
)
from paperroute_transit.core.rebuild import REBUILD_ROW, forced
from paperroute_transit.core.reducers import (
    PERSON_EVENTS,
    app_event,
    freed,
    initial_state,
    knob_step,
    ticked,
)
from paperroute_transit.core.refresh import needs_greys, noted
from paperroute_transit.core.saved import Saved, default_saved
from paperroute_transit.core.state_model import (
    Board,
    Cycle,
    State,
    elapsed,
    restarted,
    with_device,
)
from paperroute_transit.core.state_views import (
    app_banners,
    banner_live_view,
    live_stop_view,
    rendered,
    saved_of,
    settled,
    slept_view,
)

if TYPE_CHECKING:
    from paperroute_transit.app_effects import VersionWatch
    from paperroute_transit.city_parts import CityParts

QUIET_LABEL: Final[str] = 'Follow the timetable'


def fetches_reset(*, state: State) -> State:
    """
    Forget when the feeds were last fetched, so a cycle starts at once.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    State
        State with no fetch times and the cycle under way forgotten.
    """
    return restarted(
        state=replace(
            state, map=replace(state.map, fetched_at={}), last_fetch=None,
        ),
    )


def clock_restarted(*, state: State) -> State:
    """
    Start afresh once the clock is synchronised, as it may have jumped.

    Parameters
    ----------
    state : State
        State at the first synced tick's time.

    Returns
    -------
    State
        State with no departures and a load due, the app counted as
        started now and a fresh poll cycle.
    """
    return replace(
        state,
        board=Board(),
        refresh=replace(state.refresh, started_at=state.now),
        cycle=Cycle(),
    )


def clock_was_set(*, state: State, device: DeviceState) -> bool:
    """
    Say whether the kit has just ended the wait for the clock.

    Parameters
    ----------
    state : State
        State before the tick, its device fields a mirror of the kit's.
    device : DeviceState
        The device state at the tick's time.

    Returns
    -------
    bool
        True when the state waited for the clock and the kit's wait has
        ended with the clock set.
    """
    clock = device.clock
    return (
        state.clock_wait_until is not None
        and clock.wait_until is None
        and clock.set_at is not None
    )


def moved(*, before: DeviceState, after: DeviceState) -> bool:
    """
    Say whether a change to the device state may change what transit asks.

    Parameters
    ----------
    before : DeviceState
        The device state before the event.
    after : DeviceState
        The device state after it.

    Returns
    -------
    bool
        True when the panel, apart from its probe times, the device
        settings or the power action differ.
    """
    return compared(device=before) != compared(device=after)


def compared(*, device: DeviceState) -> tuple[object, ...]:
    """
    Pick the device fields a change of which settles transit.

    Parameters
    ----------
    device : DeviceState
        The device state.

    Returns
    -------
    tuple[object, ...]
        The panel without its probe times, the settings and the power
        action.
    """
    panel = replace(device.panel, probed_at=None, probing=False)
    return panel, device.settings, device.power


# pylint: disable-next=too-many-public-methods
class TransitApp(App[State]):
    """
    The transit board as an app the device kit runs.

    Every hook first refreshes the state's mirror of the device state,
    so transit's reducers keep reading it.

    Parameters
    ----------
    city : City
        The city the board runs for.
    parts : CityParts
        The city's runtime wiring; the reducer hooks never read it.
    observer : BoardObserver | None
        Told of settings and pin changes; None tells no one.
    watch : VersionWatch | None
        Reads the timetable's version before each tick; None reads
        nothing.
    """

    offline_phrase = OFFLINE_PHRASE
    quiet_label = QUIET_LABEL
    menu_rows = (REBUILD_ROW,)
    reset_hint = RESET_HINT
    confirm_copy = TRANSIT_CONFIRM_COPY

    def __init__(
        self,
        *,
        city: City,
        parts: CityParts,
        observer: BoardObserver | None = None,
        watch: VersionWatch | None = None,
    ) -> None:
        self.city = city
        self.parts = parts
        self.observer = observer
        self.watch = watch

    async def before_tick(self) -> object | None:
        """
        Read the timetable's version just before each tick.

        A read error is logged once, until a read works again, and never
        raised, so a damaged timetable leaves the loop running.

        Returns
        -------
        object | None
            A TimetableSeen the first time and on a change; None when
            unchanged, unreadable or nothing is watched.
        """
        if self.watch is None:
            return None
        return await self.watch.check()

    def observe(self, *, before: State, after: State, event: object) -> None:
        """
        Report changed settings and pins to the board's observer.

        It does not raise: the observer group swallows its members'
        errors.

        Parameters
        ----------
        before : State
            State before the event.
        after : State
            State after it.
        event : object
            The event.
        """
        if self.observer is None:
            return
        if after.settings != before.settings:
            self.observer.settings_seen(settings=after.settings)
        report_pins(
            observer=self.observer,
            before=before.places,
            state=after,
            event=event,
        )

    def start(
        self, *, section: object | None, stored: bool, device: DeviceState,
    ) -> State:
        """
        Make the state at start-up.

        Parameters
        ----------
        section : object | None
            The board section as its codec read it, or None.
        stored : bool
            Whether the section came from the file.
        device : DeviceState
            The device state at start-up.

        Returns
        -------
        State
            State from the saved section, or the city's defaults.
        """
        saved = (
            section
            if isinstance(section, Saved)
            else default_saved(city=self.city)
        )
        state = initial_state(
            saved=saved, now=device.now, stored=stored, city=self.city,
        )
        return with_device(state=state, device=device)

    def render(
        self, *, state: State, device: DeviceState, banner: Banner | None,
    ) -> object | None:
        """
        Build the live view under the banner line the kit chose.

        Parameters
        ----------
        state : State
            State.
        device : DeviceState
            The device state.
        banner : Banner | None
            The banner line, or None.

        Returns
        -------
        object | None
            The Building screen, the chooser or the Live view; None while
            the first frame waits to load, the poll cycle or the clock
            holds it, or the event left it for the next frame.
        """
        state = with_device(state=state, device=device)
        stop_view = banner_live_view(state=state, banner=banner)
        return rendered(state=state, stop_view=stop_view)

    def draw(self, *, view: object) -> Frame:
        """
        Turn a view into a frame.

        Parameters
        ----------
        view : object
            A view.

        Raises
        ------
        NotImplementedError
            Always: transit still draws through its own loop.
        """
        name = type(view).__name__
        raise NotImplementedError(f'transit draws {name} itself')

    def on_knob(
        self, *, state: State, device: DeviceState, event: KnobEvent,
    ) -> Step[State]:
        """
        Apply a knob event on the Live view.

        Parameters
        ----------
        state : State
            State.
        device : DeviceState
            The device state.
        event : KnobEvent
            The knob event.

        Returns
        -------
        Step[State]
            Nothing on the Building screen; else as `knob_step`.
        """
        state = with_device(state=state, device=device)
        if state.rebuild.missing:
            return state, ()
        return knob_step(state=state, event=event)

    def on_tick(
        self, *, state: State, device: DeviceState, tick: Tick,
    ) -> Step[State]:
        """
        Advance transit's time.

        Parameters
        ----------
        state : State
            State.
        device : DeviceState
            The device state at the tick's time.
        tick : Tick
            The tick.

        Returns
        -------
        Step[State]
            As `ticked`, after starting afresh when the clock was just
            set.
        """
        del tick
        restart = clock_was_set(state=state, device=device)
        state = with_device(state=state, device=device)
        if restart:
            state = clock_restarted(state=state)
        return ticked(state=state)

    def on_event(
        self, *, state: State, device: DeviceState, event: object,
    ) -> Step[State]:
        """
        Apply one of transit's own events.

        Parameters
        ----------
        state : State
            State.
        device : DeviceState
            The device state.
        event : object
            The event.

        Returns
        -------
        Step[State]
            As `app_event`.
        """
        state = with_device(state=state, device=device)
        return app_event(state=state, event=cast(Event, event))

    def on_device(
        self, *, state: State, before: DeviceState, after: DeviceState,
    ) -> Step[State]:
        """
        Follow a change to the device state.

        Parameters
        ----------
        state : State
            State.
        before : DeviceState
            The device state before the event.
        after : DeviceState
            The device state after the kit's handlers.

        Returns
        -------
        Step[State]
            A wake, or the last missing key set, starts a poll cycle at
            once; any change beyond the time, the clock and the probe's
            times asks for what the new state needs.
        """
        state = with_device(state=state, device=after)
        woke = before.panel.asleep and not after.panel.asleep
        keyed = before.panel.key_set is False and not after.panel.missing_keys
        if woke or keyed:
            state = fetches_reset(state=state)
        if not moved(before=before, after=after):
            return state, ()
        return settled(state=state)

    def on_row(
        self, *, state: State, device: DeviceState, row: AppRow,
    ) -> Step[State]:
        """
        Rebuild the timetable now, for its row.

        Parameters
        ----------
        state : State
            State.
        device : DeviceState
            The device state.
        row : AppRow
            The row pressed.

        Returns
        -------
        Step[State]
            A forced rebuild unless one runs.
        """
        state = with_device(state=state, device=device)
        if row != REBUILD_ROW:
            return state, ()
        return forced(state=state)

    def begin(
        self, *, state: State, device: DeviceState, event: object,
    ) -> State:
        """
        Free the frame for a person's action.

        Parameters
        ----------
        state : State
            State.
        device : DeviceState
            The device state before the event.
        event : object
            The event.

        Returns
        -------
        State
            State whose cycle is free while a person's action is handled.
        """
        del device
        return freed(state=state, free=isinstance(event, PERSON_EVENTS))

    # pylint: disable-next=too-many-arguments
    def settle(
        self,
        *,
        before: State,
        after: State,
        device: DeviceState,
        event: object,
        effects: tuple[object, ...],
    ) -> Step[State]:
        """
        Guard transit's effects, count its calls and note the event.

        Parameters
        ----------
        before : State
            State before the event.
        after : State
            State after the event and the redraw.
        device : DeviceState
            The final device state.
        event : object
            The event.
        effects : tuple[object, ...]
            Transit's effects.

        Returns
        -------
        Step[State]
            State with the cycle no longer free, the fetches counted and
            the event noted, and the effects without a rebuild during a
            reset or fetches while the feeds are held.
        """
        state = with_device(state=after, device=device)
        state = freed(state=state, free=False)
        state, kept = quiet_guard(
            result=power_guard(
                state=before,
                result=(state, cast(tuple[Effect, ...], effects)),
            ),
        )
        state = with_calls(
            state=without_skipped(state=state, event=cast(Event, event)),
            effects=kept,
        )
        state = noted(state=state, event=cast(Event, event))
        if state.held or state.wake:
            state = replace(state, held=False, wake=False)
        return state, kept

    def banners(
        self, *, state: State, device: DeviceState,
    ) -> tuple[Banner, ...]:
        """
        List transit's banner lines, first first.

        Parameters
        ----------
        state : State
            State.
        device : DeviceState
            The device state.

        Returns
        -------
        tuple[Banner, ...]
            As `app_banners`.
        """
        return app_banners(state=with_device(state=state, device=device))

    def in_menu(self, *, state: State) -> bool:
        """
        Say whether the route chooser is open.

        Parameters
        ----------
        state : State
            State.

        Returns
        -------
        bool
            True while the chooser is open.
        """
        return state.chooser is not None

    def needs_full(self, *, before: object | None, after: object) -> bool:
        """
        Say whether a change of view starts or stops the grey tiles.

        Parameters
        ----------
        before : object | None
            The view last drawn, or None.
        after : object
            The view to draw.

        Returns
        -------
        bool
            True when exactly one of the views draws grey tiles.
        """
        return needs_greys(view=cast(ScreenView | None, before)) != (
            needs_greys(view=cast(ScreenView, after))
        )

    def may_sleep(self, *, state: State, now: datetime) -> bool:
        """
        Say whether quiet hours may sleep the screen.

        Parameters
        ----------
        state : State
            State.
        now : datetime
            The time.

        Returns
        -------
        bool
            False while an auto pin window holds the panel awake.
        """
        del now
        return not holds_awake(auto=state.auto)

    def is_quiet(self, *, state: State, now: datetime) -> bool:
        """
        Say whether the shown pin's service is over for the night.

        Parameters
        ----------
        state : State
            State.
        now : datetime
            The time.

        Returns
        -------
        bool
            As `between_spans` at `now`.
        """
        return between_spans(state=replace(state, now=now))

    def wakes(self, *, state: State) -> bool:
        """
        Say whether an auto pin window's start ends a Sleep now sleep.

        Parameters
        ----------
        state : State
            State after the tick.

        Returns
        -------
        bool
            True when the tick started a window.
        """
        return state.wake

    def sleep_view(
        self, *, state: State, device: DeviceState,
    ) -> object | None:
        """
        Build the sleep screen.

        Parameters
        ----------
        state : State
            State.
        device : DeviceState
            The device state.

        Returns
        -------
        object | None
            As `slept_view`.
        """
        state = with_device(state=state, device=device)
        return slept_view(state=state, stop_view=live_stop_view(state=state))

    def device_rows(
        self, *, state: State, device: DeviceState,
    ) -> tuple[InfoRow, ...]:
        """
        List transit's lines on the Device screen.

        Parameters
        ----------
        state : State
            State.
        device : DeviceState
            The device state.

        Returns
        -------
        tuple[InfoRow, ...]
            As `transit_rows`.
        """
        return transit_rows(state=with_device(state=state, device=device))

    def status(self, *, state: State) -> Mapping[str, object]:
        """
        Gather what transit reports about itself.

        Parameters
        ----------
        state : State
            State.

        Returns
        -------
        Mapping[str, object]
            As `app_status`.
        """
        return app_status(state=state)

    def saved(self, *, state: State) -> object | None:
        """
        Take what transit keeps between restarts.

        Parameters
        ----------
        state : State
            State.

        Returns
        -------
        object | None
            As `saved_of`.
        """
        return saved_of(state=state)

    def save_due(self, *, before: State, after: State) -> bool:
        """
        Say whether an event changed what transit keeps, beyond the quota.

        Parameters
        ----------
        before : State
            State before the event.
        after : State
            State after it.

        Returns
        -------
        bool
            True when what is kept changed in more than its quota.
        """
        first, last = saved_of(state=before), saved_of(state=after)
        return first != last and not quota_only(before=first, after=last)

    def save_waits(
        self,
        *,
        written: object | None,
        current: object | None,
        written_at: datetime | None,
        now: datetime,
    ) -> bool:
        """
        Hold a change to the quota alone for `QUOTA_SAVE_EVERY`.

        Parameters
        ----------
        written : object | None
            What transit kept, as last written.
        current : object | None
            What transit keeps now.
        written_at : datetime | None
            When the kit last wrote, or None before it did.
        now : datetime
            The time.

        Returns
        -------
        bool
            True when only the quota changed and the last write is
            younger than `QUOTA_SAVE_EVERY`.
        """
        if not isinstance(written, Saved) or not isinstance(current, Saved):
            return False
        return quota_only(before=written, after=current) and not elapsed(
            since=written_at, now=now, span=QUOTA_SAVE_EVERY,
        )
