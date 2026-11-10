"""A small app that records what the kit hands it, for the kit's tests."""

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Final

import numpy as np

from paperroute_device.core.app import App
from paperroute_device.core.device_model import AppRow
from paperroute_device.core.device_state import Banner, DeviceState, KitSaved
from paperroute_device.core.kit_effects import Render
from paperroute_device.core.kit_events import Tick
from paperroute_device.core.kit_state import KitState, KitStep, Step
from paperroute_device.core.reducer import step
from paperroute_device.ports import Frame
from paperroute_device.render.canvas import HEIGHT, PAPER, WIDTH
from paperroute_hardware.core.knob_events import (
    Knob,
    KnobEvent,
    KnobHeld,
    KnobPressed,
    KnobsHeld,
    KnobTurned,
)

START: Final[datetime] = datetime(2026, 11, 10, 8, 0, tzinfo=UTC)
ZONE: Final = UTC
HELD: Final[str] = 'held'
ASK: Final[str] = 'ask'
BOTH: Final[KnobsHeld] = KnobsHeld()
DOWN: Final[KnobPressed] = KnobPressed(knob=Knob.BOTTOM)
UP: Final[KnobPressed] = KnobPressed(knob=Knob.TOP)
DO_IT: Final[AppRow] = AppRow(label='Do it')


@dataclass(frozen=True)
# pylint: disable-next=too-many-instance-attributes
class FakeState:
    """
    What the fake app knows.

    Attributes
    ----------
    view : str
        What the live view says.
    knobs : list[KnobEvent]
        The knob events handed to the app.
    settled : int
        How many times `settle` ran.
    menu : bool
        Whether the app shows a menu of its own.
    full : bool
        Whether every change of view needs a full refresh.
    sleepy : bool
        What `may_sleep` says.
    quiet : bool
        What `is_quiet` says.
    banners : tuple[Banner, ...]
        The app's banner lines.
    rows : int
        How many times one of the app's rows was pressed.
    marked : bool
        Whether `begin` marked the state for a knob hold under way.
    device : DeviceState | None
        The device state `settle` was last handed.
    blank : bool
        Whether `render` keeps the last frame.
    wake : bool
        What `wakes` says.
    """

    view: str = 'live'
    knobs: list[KnobEvent] = field(default_factory=list)
    settled: int = 0
    menu: bool = False
    full: bool = False
    sleepy: bool = True
    quiet: bool = False
    banners: tuple[Banner, ...] = ()
    rows: int = 0
    marked: bool = False
    device: DeviceState | None = None
    blank: bool = False
    wake: bool = False


class FakeApp(App[FakeState]):
    """
    An app whose hooks read and record its state.

    Parameters
    ----------
    early : object | None
        The event `before_tick` gives before each tick, or None.
    crash : bool
        Whether `perform` raises.
    """

    offline_phrase = 'cached times'
    menu_rows = (DO_IT,)

    def __init__(
        self, *, early: object | None = None, crash: bool = False,
    ) -> None:
        self.early = early
        self.crash = crash
        self.performed: list[object] = []
        self.observed: list[object] = []
        self.hooks: list[str] = []

    async def before_reset(self) -> None:
        """Note that a reset is about to run."""
        self.hooks.append('before_reset')

    async def stopped(self) -> None:
        """Note that the loop has stopped."""
        self.hooks.append('stopped')

    async def perform(
        self, *, effect: object, post: Callable[[object], None],
    ) -> None:
        """
        Record an effect; the ``'work'`` effect posts ``'worked'``.

        Parameters
        ----------
        effect : object
            The effect.
        post : Callable[[object], None]
            Posts an event.

        Raises
        ------
        RuntimeError
            When the app was made to crash.
        """
        self.performed.append(effect)
        if self.crash:
            raise RuntimeError('perform broke')
        if effect == 'work':
            post('worked')

    async def before_tick(self) -> object | None:
        """
        Give the early event.

        Returns
        -------
        object | None
            `early`.
        """
        return self.early

    async def probe(self) -> object:
        """
        Find the app's part of the facts.

        Returns
        -------
        object
            ``'probed'``.
        """
        return 'probed'

    def observe(
        self, *, before: FakeState, after: FakeState, event: object,
    ) -> None:
        """
        Record the event of each step.

        Parameters
        ----------
        before : FakeState
            Ignored.
        after : FakeState
            Ignored.
        event : object
            The event.
        """
        del before, after
        self.observed.append(event)

    def start(
        self, *, section: object | None, stored: bool, device: DeviceState,
    ) -> FakeState:
        """
        Make the fake state.

        Parameters
        ----------
        section : object | None
            Ignored.
        stored : bool
            Ignored.
        device : DeviceState
            Ignored.

        Returns
        -------
        FakeState
            A fresh state.
        """
        del section, stored, device
        return FakeState()

    def render(
        self,
        *,
        state: FakeState,
        device: DeviceState,
        banner: Banner | None,
    ) -> object | None:
        """
        Name the live view, with the banner's text.

        Parameters
        ----------
        state : FakeState
            State.
        device : DeviceState
            Ignored.
        banner : Banner | None
            The banner line.

        Returns
        -------
        object | None
            ``view:<view>`` and the banner's text; None while blank.
        """
        del device
        if state.blank:
            return None
        text = f'view:{state.view}' + ('*' if state.marked else '')
        if banner is not None:
            text = f'{text} {banner.text}'
        return text

    def draw(self, *, view: object) -> Frame:
        """
        Draw a blank frame.

        Parameters
        ----------
        view : object
            Ignored.

        Returns
        -------
        Frame
            Paper.
        """
        del view
        return np.full((HEIGHT, WIDTH), PAPER, dtype=np.uint8)

    def on_knob(
        self, *, state: FakeState, device: DeviceState, event: KnobEvent,
    ) -> Step[FakeState]:
        """
        Record a knob event; a bottom turn or a hold names a new view.

        A top press closes the app's own menu.

        Parameters
        ----------
        state : FakeState
            State.
        device : DeviceState
            Ignored.
        event : KnobEvent
            The event.

        Returns
        -------
        Step[FakeState]
            State with the event recorded; a hold shows ``held`` when
            `begin` marked the state, else ``unmarked``.
        """
        del device
        view = state.view
        if isinstance(event, KnobTurned) and event.knob is Knob.BOTTOM:
            view = f'{view}+{event.steps}'
        if isinstance(event, KnobHeld):
            view = 'held' if state.marked else 'unmarked'
        menu = state.menu and event != UP
        knobs = [*state.knobs, event]
        return replace(state, knobs=knobs, view=view, menu=menu), ()

    def on_row(
        self, *, state: FakeState, device: DeviceState, row: AppRow,
    ) -> Step[FakeState]:
        """
        Count a press of the app's row and ask for work.

        Parameters
        ----------
        state : FakeState
            State.
        device : DeviceState
            Ignored.
        row : AppRow
            Ignored.

        Returns
        -------
        Step[FakeState]
            State with the press counted, and a ``'work'`` effect.
        """
        del device, row
        return replace(state, rows=state.rows + 1), ('work',)

    def on_event(
        self, *, state: FakeState, device: DeviceState, event: object,
    ) -> Step[FakeState]:
        """
        Take a string as a new view, and ask for the effect ``'held'``.

        Parameters
        ----------
        state : FakeState
            State.
        device : DeviceState
            Ignored.
        event : object
            A string names the view; ``'ask'`` asks for ``'work'``
            instead; anything else does nothing.

        Returns
        -------
        Step[FakeState]
            State showing the view, and ``'held'`` and ``'kept'``.
        """
        del device
        if event == ASK:
            return state, ('work',)
        if not isinstance(event, str):
            return state, ()
        return replace(state, view=event), (HELD, 'kept')

    def begin(
        self, *, state: FakeState, device: DeviceState, event: object,
    ) -> FakeState:
        """
        Mark the state for a knob hold under way.

        Parameters
        ----------
        state : FakeState
            State.
        device : DeviceState
            Ignored.
        event : object
            The event.

        Returns
        -------
        FakeState
            State marked for a knob hold; unchanged otherwise.
        """
        del device
        if not isinstance(event, KnobHeld):
            return state
        return replace(state, marked=True)

    # pylint: disable-next=too-many-arguments
    def settle(
        self,
        *,
        before: FakeState,
        after: FakeState,
        device: DeviceState,
        event: object,
        effects: tuple[object, ...],
    ) -> Step[FakeState]:
        """
        Count the call, keep the device, unmark and drop ``'held'``.

        Parameters
        ----------
        before : FakeState
            Ignored.
        after : FakeState
            State after the event.
        device : DeviceState
            The final device state.
        event : object
            Ignored.
        effects : tuple[object, ...]
            The app's effects.

        Returns
        -------
        Step[FakeState]
            State counted and unmarked, and the effects without
            ``'held'``.
        """
        del before, event
        after = replace(
            after,
            settled=after.settled + 1,
            marked=False,
            device=device,
        )
        return after, tuple(effect for effect in effects if effect != HELD)

    def banners(
        self, *, state: FakeState, device: DeviceState,
    ) -> tuple[Banner, ...]:
        """
        Give the state's banner lines.

        Parameters
        ----------
        state : FakeState
            State.
        device : DeviceState
            Ignored.

        Returns
        -------
        tuple[Banner, ...]
            The lines.
        """
        del device
        return state.banners

    def in_menu(self, *, state: FakeState) -> bool:
        """
        Say whether the app shows its own menu.

        Parameters
        ----------
        state : FakeState
            State.

        Returns
        -------
        bool
            The state's flag.
        """
        return state.menu

    def needs_full(self, *, before: object | None, after: object) -> bool:
        """
        Say whether a view marked ``!`` is entered or left.

        Parameters
        ----------
        before : object | None
            The last view.
        after : object
            The view to draw.

        Returns
        -------
        bool
            True when exactly one of the views ends in ``!``.
        """
        return str(before).endswith('!') != str(after).endswith('!')

    def may_sleep(self, *, state: FakeState, now: datetime) -> bool:
        """
        Say whether the screen may sleep.

        Parameters
        ----------
        state : FakeState
            State.
        now : datetime
            Ignored.

        Returns
        -------
        bool
            The state's flag.
        """
        del now
        return state.sleepy

    def is_quiet(self, *, state: FakeState, now: datetime) -> bool:
        """
        Say whether it is the app's quiet hours.

        Parameters
        ----------
        state : FakeState
            State.
        now : datetime
            Ignored.

        Returns
        -------
        bool
            The state's flag.
        """
        del now
        return state.quiet

    def wakes(self, *, state: FakeState) -> bool:
        """
        Say whether the app ends a Sleep now sleep.

        Parameters
        ----------
        state : FakeState
            State.

        Returns
        -------
        bool
            The state's flag.
        """
        return state.wake

    def saved(self, *, state: FakeState) -> object | None:
        """
        Keep the view.

        Parameters
        ----------
        state : FakeState
            State.

        Returns
        -------
        object | None
            The view.
        """
        return state.view


APP: Final[FakeApp] = FakeApp()


def fresh(*, app: FakeState | None = None) -> KitState[FakeState]:
    """
    Make a kit state at START, written then as it stands, nothing drawn.

    Parameters
    ----------
    app : FakeState | None
        The app's state; a fresh one when None.

    Returns
    -------
    KitState[FakeState]
        The kit state.
    """
    state = app or FakeState()
    device = DeviceState(now=START)
    saved = KitSaved(device=device.settings, app=APP.saved(state=state))
    return KitState(
        device=device, app=state, saved=saved, stored=True, written_at=START,
    )


def drawn(*, app: FakeState | None = None) -> KitState[FakeState]:
    """
    Make a kit state at START with the first tick's frame drawn.

    Parameters
    ----------
    app : FakeState | None
        The app's state; a fresh one when None.

    Returns
    -------
    KitState[FakeState]
        The kit state after a tick at START.
    """
    kit, _ = kit_step(kit=fresh(app=app), event=Tick(now=START))
    return kit


def kit_step(
    *, kit: KitState[FakeState], event: object,
) -> KitStep[FakeState]:
    """
    Step the kit with the fake app in UTC.

    Parameters
    ----------
    kit : KitState[FakeState]
        Kit state.
    event : object
        What happened.

    Returns
    -------
    KitStep[FakeState]
        The next state and effects.
    """
    return step(kit=kit, app=APP, zone=ZONE, event=event)


def run(
    *, kit: KitState[FakeState], events: tuple[object, ...],
) -> KitStep[FakeState]:
    """
    Apply events in turn.

    Parameters
    ----------
    kit : KitState[FakeState]
        Kit state before.
    events : tuple[object, ...]
        What happened.

    Returns
    -------
    KitStep[FakeState]
        The state after, and the last event's effects.
    """
    effects: tuple[object, ...] = ()
    for event in events:
        kit, effects = kit_step(kit=kit, event=event)
    return kit, effects


def renders(*, effects: tuple[object, ...]) -> list[object]:
    """
    List the views drawn.

    Parameters
    ----------
    effects : tuple[object, ...]
        Effects.

    Returns
    -------
    list[object]
        Each Render's view.
    """
    return [effect.view for effect in effects if isinstance(effect, Render)]
