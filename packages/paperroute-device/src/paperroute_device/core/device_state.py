"""What the kit knows about the device, apart from any app."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from paperroute_device.core.device_model import Panel, PowerAction
from paperroute_device.core.device_settings import DeviceSettings


@dataclass(frozen=True)
class ClockWait:
    """
    The wait for the clock to be synchronised.

    Attributes
    ----------
    wait_until : datetime | None
        When to stop waiting, or None when not waiting.
    gave_up : bool
        True once the wait has run out unsynchronised; the hold is over
        for good, but a later sync still starts afresh.
    known : bool
        False from start-up until the first tick says whether the clock
        is synchronised; the kit waits for the clock meanwhile.
    set_at : datetime | None
        When the clock was last found synchronised after a wait, or
        None.
    """

    wait_until: datetime | None = None
    gave_up: bool = False
    known: bool = True
    set_at: datetime | None = None


@dataclass(frozen=True)
# pylint: disable-next=too-many-instance-attributes
class DeviceState:
    """
    The device's own state.

    Attributes
    ----------
    now : datetime
        The latest time seen.
    panel : Panel
        The menu, confirm, Wi-Fi setup, sleep, keys and facts.
    settings : DeviceSettings
        The device's own settings.
    power : PowerAction | None
        The power action under way; while set, nothing is redrawn.
    clock : ClockWait
        The wait for the clock.
    last_view : object | None
        The view last drawn.
    last_was_sleep : bool
        Whether the view last drawn was the sleep screen.
    in_menu : bool
        Whether the view last drawn was a menu.
    started_at : datetime | None
        The time of the first event, or None before it.
    touched_at : datetime | None
        The last knob touch, or None before the first.
    """

    now: datetime
    panel: Panel = field(default_factory=Panel)
    settings: DeviceSettings = field(default_factory=DeviceSettings)
    power: PowerAction | None = None
    clock: ClockWait = field(default_factory=ClockWait)
    last_view: object | None = None
    last_was_sleep: bool = False
    in_menu: bool = False
    started_at: datetime | None = None
    touched_at: datetime | None = None


@dataclass(frozen=True)
class KitSaved:
    """
    What the device keeps between restarts.

    Attributes
    ----------
    device : DeviceSettings
        The device's own settings.
    app : object | None
        What the app keeps, or None.
    """

    device: DeviceSettings
    app: object | None


@dataclass(frozen=True)
class Banner:
    """
    One line the banner may show.

    Attributes
    ----------
    text : str
        The line.
    warning : bool
        Whether it is drawn as a warning.
    planned : bool
        Whether it tells of planned work.
    more : int
        How many more lines of the same kind wait behind it.
    """

    text: str
    warning: bool = True
    planned: bool = False
    more: int = 0


type DeviceStep = tuple[DeviceState, tuple[object, ...]]


def clock_waiting(*, device: DeviceState) -> bool:
    """
    Say whether the device still waits for the clock to be synchronised.

    Parameters
    ----------
    device : DeviceState
        Device state.

    Returns
    -------
    bool
        True before the first tick, and while a wait is set, has not been
        given up, and its end has not come, in real time.
    """
    clock = device.clock
    if not clock.known:
        return True
    if clock.wait_until is None or clock.gave_up:
        return False
    return device.now.timestamp() < clock.wait_until.timestamp()


def elapsed(
    *, since: datetime | None, now: datetime, span: timedelta,
) -> bool:
    """
    Say whether a span of real time has passed since a moment.

    Parameters
    ----------
    since : datetime | None
        The moment, or None for never.
    now : datetime
        Current time.
    span : timedelta
        Span of real time.

    Returns
    -------
    bool
        True when `since` is None or at least `span` ago.
    """
    if since is None:
        return True
    return now.timestamp() - since.timestamp() >= span.total_seconds()
