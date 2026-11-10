"""A clock that shows the time, built on the device kit alone."""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Final

from paperroute_device.core.app import App, MigrationStep
from paperroute_device.core.device_model import Credential, DeviceKey
from paperroute_device.core.device_state import Banner, DeviceState
from paperroute_device.core.kit_events import Tick
from paperroute_device.core.kit_state import Step
from paperroute_device.ports import Frame
from paperroute_device.render.canvas import HEIGHT, INK, WIDTH, Canvas, Face
from paperroute_device.render.frame import finish
from paperroute_device_testkit.device_fakes import FakeKeys
from paperroute_hardware.core.knob_events import KnobEvent

TIME_FACE: Final[Face] = Face(weight=800, px=96)
BANNER_FACE: Final[Face] = Face(weight=400, px=24)
BANNER_GAP: Final[int] = 96  # pixels below the time's middle
DEMO_CREDENTIAL: Final[Credential] = Credential(
    label='Demo key', refusal='Bad demo key.', not_saved='Demo key not saved.',
)


@dataclass(frozen=True)
class ClockState:
    """
    What the clock knows.

    Attributes
    ----------
    now : datetime
        The time shown.
    """

    now: datetime


@dataclass(frozen=True)
class ClockView:
    """
    The clock's live view.

    Attributes
    ----------
    text : str
        The time, hours and minutes.
    banner : str | None
        The banner line under it, or None.
    """

    text: str
    banner: str | None


class ClockApp(App[ClockState]):
    """
    Shows the time, with the kit's banner line under it.

    Parameters
    ----------
    steps : Mapping[int, MigrationStep] | None
        The app's state file steps; none when None.
    """

    def __init__(
        self, *, steps: Mapping[int, MigrationStep] | None = None,
    ) -> None:
        self.steps = dict(steps or {})

    def migrations(self) -> Mapping[int, MigrationStep]:
        """
        Give the clock's state file steps.

        Returns
        -------
        Mapping[int, MigrationStep]
            The steps it was made with.
        """
        return self.steps

    def keys(self) -> tuple[DeviceKey, ...]:
        """
        Declare the one demo key, held nowhere.

        Returns
        -------
        tuple[DeviceKey, ...]
            The demo key, unset.
        """
        return (
            DeviceKey(
                name='demo',
                credential=DEMO_CREDENTIAL,
                store=FakeKeys(),
                display_name='Demo',
            ),
        )

    def start(
        self, *, section: object | None, stored: bool, device: DeviceState,
    ) -> ClockState:
        """
        Start at the device's time.

        Parameters
        ----------
        section : object | None
            Ignored; the clock keeps no section.
        stored : bool
            Ignored.
        device : DeviceState
            The device state at start-up.

        Returns
        -------
        ClockState
            The time now.
        """
        del section, stored
        return ClockState(now=device.now)

    def render(
        self,
        *,
        state: ClockState,
        device: DeviceState,
        banner: Banner | None,
    ) -> object | None:
        """
        Show the time and the banner's text.

        Parameters
        ----------
        state : ClockState
            State.
        device : DeviceState
            Ignored.
        banner : Banner | None
            The banner line the kit chose.

        Returns
        -------
        object | None
            The clock's view.
        """
        del device
        return ClockView(
            text=f'{state.now:%H:%M}',
            banner=banner.text if banner else None,
        )

    def draw(self, *, view: object) -> Frame:
        """
        Draw the time centred, and the banner line under it.

        Parameters
        ----------
        view : object
            A `ClockView`.

        Returns
        -------
        Frame
            480 by 800 pixels, 0 or 255.
        """
        assert isinstance(view, ClockView)
        canvas = Canvas()
        middle = (WIDTH / 2, HEIGHT / 2)
        canvas.text(
            at=middle, text=view.text, face=TIME_FACE, grey=INK, anchor='mm',
        )
        if view.banner:
            canvas.text(
                at=(middle[0], middle[1] + BANNER_GAP),
                text=view.banner,
                face=BANNER_FACE,
                grey=INK,
                anchor='mm',
            )
        return finish(canvas)

    def on_knob(
        self, *, state: ClockState, device: DeviceState, event: KnobEvent,
    ) -> Step[ClockState]:
        """
        Ignore the knobs.

        Parameters
        ----------
        state : ClockState
            State.
        device : DeviceState
            Ignored.
        event : KnobEvent
            Ignored.

        Returns
        -------
        Step[ClockState]
            The state unchanged and no effects.
        """
        del device, event
        return state, ()

    def on_tick(
        self, *, state: ClockState, device: DeviceState, tick: Tick,
    ) -> Step[ClockState]:
        """
        Move to the tick's time.

        Parameters
        ----------
        state : ClockState
            State.
        device : DeviceState
            Ignored.
        tick : Tick
            The tick.

        Returns
        -------
        Step[ClockState]
            The state at the tick's time and no effects.
        """
        del device
        return replace(state, now=tick.now), ()
