"""The kit's state: the device's beside the app's own."""

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Final

from paperroute_device.core.device_state import DeviceState, KitSaved

SAVE_WAIT: Final[timedelta] = timedelta(seconds=3)


@dataclass(frozen=True)
class KitState[S]:
    """
    Everything the kit carries.

    Attributes
    ----------
    device : DeviceState
        The device's own state.
    app : S
        The app's state, which the kit never looks inside.
    running : bool
        False once the board should stop.
    changed_at : datetime | None
        When what is saved last changed, or None.
    saved : KitSaved | None
        What was last written, or None before anything was.
    stored : bool
        Whether the state at start-up came from the file.
    written_at : datetime | None
        When what is kept was last written, or None before anything
        was.
    """

    device: DeviceState
    app: S
    running: bool = True
    changed_at: datetime | None = None
    saved: KitSaved | None = None
    stored: bool = False
    written_at: datetime | None = None


def with_device[S](*, kit: KitState[S], device: DeviceState) -> KitState[S]:
    """
    Set the device state.

    Parameters
    ----------
    kit : KitState[S]
        Kit state.
    device : DeviceState
        The new device state.

    Returns
    -------
    KitState[S]
        Kit state with the device set; `kit` itself when `device` is
        the one it holds.
    """
    if device is kit.device:
        return kit
    return replace(kit, device=device)


def with_app[S](*, kit: KitState[S], app: S) -> KitState[S]:
    """
    Set the app's state.

    Parameters
    ----------
    kit : KitState[S]
        Kit state.
    app : S
        The app's new state.

    Returns
    -------
    KitState[S]
        Kit state with the app's state set; `kit` itself when `app` is
        the one it holds.
    """
    if app is kit.app:
        return kit
    return replace(kit, app=app)


type Step[T] = tuple[T, tuple[object, ...]]
type KitStep[T] = tuple[KitState[T], tuple[object, ...]]
