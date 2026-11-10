"""Tests for what the kit shows and how it draws it."""

from dataclasses import replace

import numpy as np

from paperroute_device.core.device_events import ApiKeyChanged
from paperroute_device.core.device_model import Panel
from paperroute_device.core.device_state import Banner, DeviceState
from paperroute_device.core.kit_effects import Render
from paperroute_device.core.kit_events import Tick
from paperroute_device.core.panel_views import PlainSleepView
from paperroute_device.core.shown import render_of, rendered
from paperroute_device.core.sleep import SLEEP_BAND
from paperroute_device.render.canvas import HEIGHT, INK, PAPER, WIDTH
from paperroute_device.render.sleep import BAND_H, draw_plain_sleep
from tests.core.fake_app import (
    APP,
    START,
    ZONE,
    FakeState,
    drawn,
    fresh,
    kit_step,
    renders,
)

KEY_LINE = 'No Test key · set it on your phone'


def test_none_view_keeps_last_frame() -> None:
    """A view of None draws nothing and keeps the last frame recorded."""
    kit = drawn()
    kit = replace(kit, app=replace(kit.app, blank=True))
    after, effects = kit_step(kit=kit, event='other')
    assert after.app.view == 'other'
    assert not renders(effects=effects)
    assert after.device.last_view == 'view:live'


def test_sleep_default_is_plain_band() -> None:
    """With no sleep view of its own an app sleeps on paper under the band."""
    view = APP.sleep_view(state=FakeState(), device=DeviceState(now=START))
    assert view == PlainSleepView(band=SLEEP_BAND)
    assert isinstance(view, PlainSleepView)
    frame = draw_plain_sleep(view=view)
    assert frame.shape == (HEIGHT, WIDTH)
    assert frame.dtype == np.uint8
    assert (frame[:-BAND_H] == PAPER).all()
    assert (frame[-BAND_H:] == INK).mean() > 0.8


def test_render_of_full_and_clear_rules() -> None:
    """First and sleep frames are full; menus clear in and full out."""
    first = DeviceState(now=START)
    assert render_of(device=first, app=APP, view='a', menu=False) == Render(
        view='a', full=True,
    )
    shown = replace(first, last_view='a')
    woke = replace(shown, last_was_sleep=True)
    asleep = replace(shown, panel=Panel(asleep=True))
    for device in (woke, asleep):
        render = render_of(device=device, app=APP, view='b', menu=False)
        assert render == Render(view='b', full=True)
    entered = render_of(device=shown, app=APP, view='b', menu=True)
    assert entered == Render(view='b', clear=True)
    left = render_of(
        device=replace(shown, in_menu=True), app=APP, view='b', menu=False,
    )
    assert left == Render(view='b', full=True)
    greys = render_of(device=shown, app=APP, view='b!', menu=False)
    assert greys == Render(view='b!', full=True)
    in_menu = replace(shown, in_menu=True)
    assert render_of(
        device=in_menu, app=APP, view='b!', menu=True,
    ) == Render(view='b!')
    assert render_of(device=shown, app=APP, view='b', menu=False) == Render(
        view='b',
    )


def test_app_menu_draws_clear_then_full() -> None:
    """The app's own menu counts as a menu without the kit knowing it."""
    kit = drawn()
    kit = replace(kit, app=replace(kit.app, menu=True, view='chooser'))
    kit, effects = rendered(kit=kit, app=APP, zone=ZONE)
    assert effects == (Render(view='view:chooser', clear=True),)
    assert kit.device.in_menu
    kit = replace(kit, app=replace(kit.app, menu=False, view='live'))
    _, effects = rendered(kit=kit, app=APP, zone=ZONE)
    assert effects == (Render(view='view:live', full=True),)


def test_first_frame_is_full() -> None:
    """The first tick draws the live view in full."""
    _, effects = kit_step(kit=fresh(), event=Tick(now=START))
    assert renders(effects=effects) == ['view:live']
    assert Render(view='view:live', full=True) in effects


def test_banner_line_reaches_render() -> None:
    """The device's line reaches `render`; the app's shows only alone."""
    missing = ApiKeyChanged(missing=('Test',))
    kit, effects = kit_step(kit=drawn(), event=missing)
    assert renders(effects=effects) == [f'view:live {KEY_LINE}']
    lined = FakeState(banners=(Banner(text='App line'),))
    _, effects = kit_step(kit=drawn(app=lined), event=missing)
    assert renders(effects=effects) == [f'view:live {KEY_LINE}']
    assert drawn(app=lined).device.last_view == 'view:live App line'
