"""Tests for transit's reducers behind the device kit's App contract."""

from dataclasses import replace

from paperroute_device.core.device_state import ClockWait
from paperroute_sydney.city import SYDNEY
from paperroute_transit.core.messages import HolidaysLoaded
from paperroute_transit.core.panel_screens import TRANSIT_CONFIRM_COPY
from paperroute_transit.core.panel_views import SleepView
from paperroute_transit.core.rebuild import REBUILD_ROW
from paperroute_transit.core.state import REDUCER_PARTS, device_of
from paperroute_transit.transit_app import TransitApp
from tests.core.test_state import fresh, loaded

APP = TransitApp(city=SYDNEY, parts=REDUCER_PARTS)


def test_hooks_match_the_spec() -> None:
    """Transit's phrase, label, rows and confirm copy are today's."""
    assert APP.offline_phrase == 'timetable times'
    assert APP.quiet_label == 'Follow the timetable'
    assert APP.menu_rows == (REBUILD_ROW,)
    assert APP.confirm_copy == TRANSIT_CONFIRM_COPY


def test_render_holds_as_before() -> None:
    """A late answer, the clock wait and a drawn sleep frame keep the frame."""
    state = loaded(state=fresh())
    device = device_of(state=state)
    assert APP.render(state=state, device=device, banner=None) is not None
    held, _ = APP.on_event(
        state=state,
        device=device,
        event=HolidaysLoaded(dates=frozenset()),
    )
    assert APP.render(state=held, device=device, banner=None) is None
    waiting = replace(device, clock=ClockWait(known=False))
    assert APP.render(state=state, device=waiting, banner=None) is None
    asleep = replace(device, panel=replace(device.panel, asleep=True))
    view = APP.sleep_view(state=state, device=asleep)
    assert isinstance(view, SleepView)
    drawn = replace(asleep, last_view=replace(view, loading=False))
    assert APP.sleep_view(state=state, device=drawn) is None
