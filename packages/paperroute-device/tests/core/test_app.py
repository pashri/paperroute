"""Tests for the App contract's defaults."""

import numpy as np
import pytest

from paperroute_device.core.app import KIT_RESET_HINT, App
from paperroute_device.core.device_model import AppRow
from paperroute_device.core.device_state import Banner, DeviceState
from paperroute_device.core.kit_events import Tick
from paperroute_device.core.kit_state import Step
from paperroute_device.core.panel_views import KIT_CONFIRM_COPY, PlainSleepView
from paperroute_device.core.sleep import SLEEP_BAND
from paperroute_device.ports import Frame
from paperroute_hardware.core.knob_events import Knob, KnobEvent, KnobPressed
from tests.core.fake_app import START


class Bare(App[int]):
    """An app with only the required hooks."""

    def start(
        self, *, section: object | None, stored: bool, device: DeviceState,
    ) -> int:
        """
        Start at zero.

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
        int
            Zero.
        """
        del section, stored, device
        return 0

    def render(
        self, *, state: int, device: DeviceState, banner: Banner | None,
    ) -> object | None:
        """
        Show the count.

        Parameters
        ----------
        state : int
            The count.
        device : DeviceState
            Ignored.
        banner : Banner | None
            Ignored.

        Returns
        -------
        object | None
            The count.
        """
        del device, banner
        return state

    def draw(self, *, view: object) -> Frame:
        """
        Draw nothing.

        Parameters
        ----------
        view : object
            Ignored.

        Returns
        -------
        Frame
            An empty frame.
        """
        del view
        return np.zeros((1, 1), dtype=np.uint8)

    def on_knob(
        self, *, state: int, device: DeviceState, event: KnobEvent,
    ) -> Step[int]:
        """
        Count a knob event.

        Parameters
        ----------
        state : int
            The count.
        device : DeviceState
            Ignored.
        event : KnobEvent
            Ignored.

        Returns
        -------
        Step[int]
            The count plus one.
        """
        del device, event
        return state + 1, ()


def test_defaults_are_the_kit_ones() -> None:
    """Every optional hook falls back to the kit's own default."""
    app, device = Bare(), DeviceState(now=START)
    assert app.offline_phrase == ''
    assert app.quiet_label is None
    assert app.menu_rows == ()
    assert app.reset_hint == KIT_RESET_HINT
    assert app.confirm_copy == KIT_CONFIRM_COPY
    assert app.on_tick(state=1, device=device, tick=Tick(now=START)) == (1, ())
    assert app.on_event(state=1, device=device, event='x') == (1, ())
    assert app.on_device(state=1, before=device, after=device) == (1, ())
    row = AppRow(label='Row')
    assert app.on_row(state=1, device=device, row=row) == (1, ())
    assert app.begin(state=1, device=device, event='x') == 1
    assert app.settle(
        before=0, after=1, device=device, event='x', effects=('e',),
    ) == (1, ('e',))
    assert app.banners(state=1, device=device) == ()
    assert not app.in_menu(state=1)
    assert not app.needs_full(before='a', after='b')
    assert app.may_sleep(state=1, now=START)
    assert not app.is_quiet(state=1, now=START)
    assert app.sleep_view(state=1, device=device) == PlainSleepView(
        band=SLEEP_BAND,
    )
    assert app.device_rows(state=1, device=device) == ()
    assert app.status(state=1) == {}
    assert app.saved(state=1) is None
    assert not app.save_due(before=0, after=1)
    app.observe(before=0, after=1, event='x')
    press = KnobPressed(knob=Knob.TOP)
    assert app.on_knob(state=1, device=device, event=press) == (2, ())


def test_required_hooks_are_abstract() -> None:
    """An app missing a required hook cannot be made."""
    with pytest.raises(TypeError):
        App()  # type: ignore[abstract]
