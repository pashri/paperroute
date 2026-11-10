"""The contract an app implements to run on the device kit."""

import argparse
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from datetime import datetime, tzinfo
from pathlib import Path
from typing import TYPE_CHECKING, Final, Protocol

from paperroute_device.core.device_model import AppRow, DeviceKey
from paperroute_device.core.device_state import Banner, DeviceState
from paperroute_device.core.kit_events import Tick
from paperroute_device.core.kit_state import Step
from paperroute_device.core.panel_views import (
    KIT_CONFIRM_COPY,
    ConfirmCopy,
    InfoRow,
    PlainSleepView,
)
from paperroute_device.core.sleep import SLEEP_BAND
from paperroute_device.ports import Clock, DeviceObserver, Frame
from paperroute_hardware.core.knob_events import KnobEvent

if TYPE_CHECKING:
    from paperroute_device.logs import LastError
    from paperroute_device.runtime.app_web import AppWeb
    from paperroute_device.runtime.timings import KitTimings

KIT_RESET_HINT: Final[str] = (
    'Reset settings clears the settings, Wi-Fi and the keys. Erase '
    'everything also deletes the saved data. Both are confirmed on the '
    'device.'
)

type MigrationStep = Callable[[dict[str, object]], dict[str, object]]


class SectionCodec(Protocol):
    """Reads and writes the app's section of the state file."""

    def load(self, *, data: object) -> object:
        """
        Read the app's section.

        Parameters
        ----------
        data : object
            The section as read from the file.

        Returns
        -------
        object
            What the app keeps.

        Raises
        ------
        ValueError
            When the section is unusable.
        """

    def dump(self, *, saved: object) -> dict[str, object]:
        """
        Write the app's section.

        Parameters
        ----------
        saved : object
            What the app keeps, from `App.saved`.

        Returns
        -------
        dict[str, object]
            The section to write.
        """


# pylint: disable-next=too-many-public-methods
class App[S](ABC):
    """
    An app the kit runs: its live view, its knobs and its own state.

    The kit owns the menu, confirm, Wi-Fi setup, sleep, the banner's
    device lines and the device state, and calls the app only for what
    none of those claims. `S` is the app's own state, which the kit
    carries without looking inside.

    Attributes
    ----------
    offline_phrase : str
        What the No Wi-Fi banner says still works, or empty.
    quiet_label : str | None
        The label of the app's quiet mode, or None to offer only My
        hours.
    menu_rows : tuple[AppRow, ...]
        The app's rows on the Settings menu.
    reset_hint : str
        The Device page's line on what the resets clear.
    confirm_copy : ConfirmCopy
        The line each confirm says happens.
    """

    offline_phrase: str = ''
    quiet_label: str | None = None
    menu_rows: tuple[AppRow, ...] = ()
    reset_hint: str = KIT_RESET_HINT
    confirm_copy: ConfirmCopy = KIT_CONFIRM_COPY

    # pylint: disable-next=useless-return
    def section(self) -> SectionCodec | None:
        """
        Give the codec of the app's section of the state file.

        Returns
        -------
        SectionCodec | None
            By default None: the app keeps no section.
        """
        return None

    def migrations(self) -> Mapping[int, MigrationStep]:
        """
        Give the app's steps in the state file's one migration chain.

        Returns
        -------
        Mapping[int, MigrationStep]
            The step that brings each version up one; by default none.
        """
        return {}

    # pylint: disable-next=useless-return
    def zone(self) -> tzinfo | None:
        """
        Give the app's local time zone.

        Returns
        -------
        tzinfo | None
            By default None: the app has no time zone.
        """
        return None

    async def perform(
        self, *, effect: object, post: Callable[[object], None],
    ) -> None:
        """
        Carry out one of the app's own effects, in the background.

        Parameters
        ----------
        effect : object
            An effect the app's hooks asked for.
        post : Callable[[object], None]
            Posts an event to the kit's queue.

        Raises
        ------
        ValueError
            By default for every effect: the app asks for none.
        """
        del post
        raise ValueError(f'unknown effect {effect!r}')

    # pylint: disable-next=useless-return
    async def before_tick(self) -> object | None:
        """
        Find an event to post just before each tick.

        It must not raise: an app logs and swallows its own read errors,
        so a failing read leaves the loop running.

        Returns
        -------
        object | None
            The event, or None to post nothing; by default None.
        """
        return None

    async def before_reset(self) -> None:
        """
        Stop whatever of the app's own work a reset must not race.

        Awaited before a reset deletes any file; by default nothing.
        """

    async def stopped(self) -> None:
        """
        Close what the app holds once the loop has stopped.

        Awaited after the loop's tasks are cancelled; by default nothing.
        """

    # pylint: disable-next=useless-return
    def clock(self) -> Clock | None:
        """
        Give the clock the run keeps, once the app's adapters are open.

        Returns
        -------
        Clock | None
            By default None: the system clock in the app's zone.
        """
        return None

    def replaying(self) -> bool:
        """
        Say whether the run replays recordings rather than running live.

        Asked once the app's adapters are open. A replay keeps no keys,
        probes nothing, resets nothing and serves no device pages.

        Returns
        -------
        bool
            By default False.
        """
        return False

    # pylint: disable-next=useless-return
    async def probe(self) -> object:
        """
        Find out what the app reports with the device's facts.

        Returns
        -------
        object
            The facts' `app` part; by default None.
        """
        return None

    def timings(self) -> KitTimings:
        """
        Make the one timings object the board reports to.

        Returns
        -------
        KitTimings
            By default the kit's own.
        """
        # pylint: disable-next=import-outside-toplevel
        from paperroute_device.runtime.timings import KitTimings

        return KitTimings()

    def add_arguments(self, *, parser: argparse.ArgumentParser) -> None:
        """
        Add the app's own flags to the run command.

        Parameters
        ----------
        parser : argparse.ArgumentParser
            The run command's parser, holding the kit's flags already.
        """
        del parser

    @contextmanager
    def opened(
        self, *, args: argparse.Namespace, last_error: LastError,
    ) -> Iterator[None]:
        """
        Open the app's adapters for the run, and close them after it.

        Parameters
        ----------
        args : argparse.Namespace
            The run command's arguments.
        last_error : LastError
            Holds the latest error logged.

        Yields
        ------
        None
            While the app's adapters are open; by default none are.
        """
        del args, last_error
        yield

    def keys(self) -> tuple[DeviceKey, ...]:
        """
        Give the API keys the device keeps for the app.

        Returns
        -------
        tuple[DeviceKey, ...]
            The keys, in declaration order; by default none.
        """
        return ()

    def erased(self, *, args: argparse.Namespace) -> tuple[Path, ...]:
        """
        Name the app's files that Erase everything deletes.

        Parameters
        ----------
        args : argparse.Namespace
            The run command's arguments.

        Returns
        -------
        tuple[Path, ...]
            The files, each inside the data directory; by default none.
        """
        del args
        return ()

    def web(self, *, args: argparse.Namespace, timings: KitTimings) -> AppWeb:
        """
        Give the app's part of the web pages.

        Called once the app's adapters are open.

        Parameters
        ----------
        args : argparse.Namespace
            The run command's arguments.
        timings : KitTimings
            The timings object from `timings`, which the pages read.

        Returns
        -------
        AppWeb
            By default the kit's nav and hint and nothing else.
        """
        # pylint: disable-next=import-outside-toplevel
        from paperroute_device.runtime.app_web import AppWeb

        del args, timings
        return AppWeb()

    def observers(self) -> tuple[DeviceObserver, ...]:
        """
        Give the app's own observers, beside the timings.

        Returns
        -------
        tuple[DeviceObserver, ...]
            By default none.
        """
        return ()

    @abstractmethod
    def start(
        self, *, section: object | None, stored: bool, device: DeviceState,
    ) -> S:
        """
        Make the app's state at start-up.

        Parameters
        ----------
        section : object | None
            The app's section as its codec read it, or None.
        stored : bool
            Whether the section came from the file.
        device : DeviceState
            The device state at start-up.

        Returns
        -------
        S
            The app's state.
        """

    @abstractmethod
    def render(
        self, *, state: S, device: DeviceState, banner: Banner | None,
    ) -> object | None:
        """
        Build the live view to show.

        Parameters
        ----------
        state : S
            The app's state.
        device : DeviceState
            The device state.
        banner : Banner | None
            The banner line the kit chose, or None.

        Returns
        -------
        object | None
            The view, compared for equality with the last one; None
            keeps the last frame.
        """

    @abstractmethod
    def draw(self, *, view: object) -> Frame:
        """
        Turn a view into a frame.

        Parameters
        ----------
        view : object
            A view `render` or `sleep_view` gave, or one of the kit's.

        Returns
        -------
        Frame
            The frame.
        """

    @abstractmethod
    def on_knob(
        self, *, state: S, device: DeviceState, event: KnobEvent,
    ) -> Step[S]:
        """
        Apply a knob event no device concern claimed.

        Parameters
        ----------
        state : S
            The app's state.
        device : DeviceState
            The device state.
        event : KnobEvent
            The knob event.

        Returns
        -------
        Step[S]
            The app's new state and effects.
        """

    def on_tick(
        self, *, state: S, device: DeviceState, tick: Tick,
    ) -> Step[S]:
        """
        Advance the app's time.

        Parameters
        ----------
        state : S
            The app's state.
        device : DeviceState
            The device state at the tick's time.
        tick : Tick
            The tick.

        Returns
        -------
        Step[S]
            By default the state unchanged and no effects.
        """
        del device, tick
        return state, ()

    def on_event(
        self, *, state: S, device: DeviceState, event: object,
    ) -> Step[S]:
        """
        Apply an event the kit does not handle.

        Parameters
        ----------
        state : S
            The app's state.
        device : DeviceState
            The device state.
        event : object
            The event.

        Returns
        -------
        Step[S]
            By default the state unchanged and no effects.
        """
        del device, event
        return state, ()

    def on_device(
        self, *, state: S, before: DeviceState, after: DeviceState,
    ) -> Step[S]:
        """
        Follow a change to the device state.

        Parameters
        ----------
        state : S
            The app's state.
        before : DeviceState
            The device state before the event.
        after : DeviceState
            The device state after the kit's handlers.

        Returns
        -------
        Step[S]
            By default the state unchanged and no effects.
        """
        del before, after
        return state, ()

    def on_row(
        self, *, state: S, device: DeviceState, row: AppRow,
    ) -> Step[S]:
        """
        Do what one of the app's menu rows does.

        Parameters
        ----------
        state : S
            The app's state.
        device : DeviceState
            The device state.
        row : AppRow
            The row pressed.

        Returns
        -------
        Step[S]
            By default the state unchanged and no effects; the kit then
            shows the Device screen.
        """
        del device, row
        return state, ()

    def begin(self, *, state: S, device: DeviceState, event: object) -> S:
        """
        Prepare the app's state before the kit handles an event.

        Parameters
        ----------
        state : S
            The app's state.
        device : DeviceState
            The device state before the event.
        event : object
            The event.

        Returns
        -------
        S
            By default the state unchanged.
        """
        del device, event
        return state

    # pylint: disable-next=too-many-arguments
    def settle(
        self,
        *,
        before: S,
        after: S,
        device: DeviceState,
        event: object,
        effects: tuple[object, ...],
    ) -> Step[S]:
        """
        Wrap the app's part of an event's result.

        Parameters
        ----------
        before : S
            The app's state before the event.
        after : S
            The app's state after the event and the redraw.
        device : DeviceState
            The final device state.
        event : object
            The event.
        effects : tuple[object, ...]
            The app's effects; the kit's own are not among them.

        Returns
        -------
        Step[S]
            By default `after` and `effects` unchanged.
        """
        del before, device, event
        return after, effects

    def banners(
        self, *, state: S, device: DeviceState,
    ) -> tuple[Banner, ...]:
        """
        List the app's banner lines, first first.

        Parameters
        ----------
        state : S
            The app's state.
        device : DeviceState
            The device state.

        Returns
        -------
        tuple[Banner, ...]
            By default none. The kit's own lines rank first.
        """
        del state, device
        return ()

    def in_menu(self, *, state: S) -> bool:
        """
        Say whether the app shows a menu of its own.

        Parameters
        ----------
        state : S
            The app's state.

        Returns
        -------
        bool
            By default False.
        """
        del state
        return False

    def needs_full(self, *, before: object | None, after: object) -> bool:
        """
        Say whether a change of view needs a full refresh.

        Parameters
        ----------
        before : object | None
            The view last drawn, or None.
        after : object
            The view to draw.

        Returns
        -------
        bool
            By default False.
        """
        del before, after
        return False

    def may_sleep(self, *, state: S, now: datetime) -> bool:
        """
        Say whether quiet hours may sleep the screen, in any mode.

        Parameters
        ----------
        state : S
            The app's state.
        now : datetime
            The time.

        Returns
        -------
        bool
            By default True.
        """
        del state, now
        return True

    def is_quiet(self, *, state: S, now: datetime) -> bool:
        """
        Say whether it is quiet hours by the app's own mode.

        Parameters
        ----------
        state : S
            The app's state.
        now : datetime
            The time.

        Returns
        -------
        bool
            By default False.
        """
        del state, now
        return False

    def wakes(self, *, state: S) -> bool:
        """
        Say whether the app ends a Sleep now sleep.

        Asked after each tick while the screen sleeps by hand; quiet
        hours alone end a quiet sleep.

        Parameters
        ----------
        state : S
            The app's state after the tick.

        Returns
        -------
        bool
            By default False.
        """
        del state
        return False

    def sleep_view(
        self, *, state: S, device: DeviceState,
    ) -> object | None:
        """
        Build the sleep screen.

        Parameters
        ----------
        state : S
            The app's state.
        device : DeviceState
            The device state.

        Returns
        -------
        object | None
            By default the kit's plain sleep screen; None keeps the last
            frame.
        """
        del state, device
        return PlainSleepView(band=SLEEP_BAND)

    def device_rows(
        self, *, state: S, device: DeviceState,
    ) -> tuple[InfoRow, ...]:
        """
        List the app's lines on the Device screen.

        Parameters
        ----------
        state : S
            The app's state.
        device : DeviceState
            The device state.

        Returns
        -------
        tuple[InfoRow, ...]
            By default none.
        """
        del state, device
        return ()

    def status(self, *, state: S) -> Mapping[str, object]:
        """
        Gather what the app reports about itself.

        It must not raise.

        Parameters
        ----------
        state : S
            The app's state.

        Returns
        -------
        Mapping[str, object]
            By default nothing.
        """
        del state
        return {}

    # pylint: disable-next=useless-return
    def saved(self, *, state: S) -> object | None:
        """
        Take what the app keeps between restarts.

        Parameters
        ----------
        state : S
            The app's state.

        Returns
        -------
        object | None
            By default None.
        """
        del state
        return None

    def save_due(self, *, before: S, after: S) -> bool:
        """
        Say whether an event changed what the app keeps.

        Parameters
        ----------
        before : S
            The app's state before the event.
        after : S
            The app's state after it.

        Returns
        -------
        bool
            By default whether `saved` differs.
        """
        return self.saved(state=before) != self.saved(state=after)

    def save_waits(
        self,
        *,
        written: object | None,
        current: object | None,
        written_at: datetime | None,
        now: datetime,
    ) -> bool:
        """
        Say whether a change to the app's part alone may wait to be saved.

        Parameters
        ----------
        written : object | None
            The app's part as last written.
        current : object | None
            The app's part now, which differs from `written`.
        written_at : datetime | None
            When the kit last wrote, or None before it did.
        now : datetime
            The time.

        Returns
        -------
        bool
            By default False, so the change is saved `SAVE_WAIT` after
            it was made. A change to the device settings never asks.
        """
        del written, current, written_at, now
        return False

    def observe(self, *, before: S, after: S, event: object) -> None:
        """
        Report a step to the app's own observers.

        It must not raise.

        Parameters
        ----------
        before : S
            The app's state before the event.
        after : S
            The app's state after it.
        event : object
            The event.
        """
        del before, after, event
