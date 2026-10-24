"""Tests for the Alerts view and what the knobs do on it."""

from dataclasses import replace
from datetime import datetime, timedelta

from paperroute.core.alert_match import Listed, listed_alerts
from paperroute.core.alert_model import Alert, AlertTie, Matched
from paperroute.core.alerts_view import ALERT_ROWS, AlertRow, build_alerts_view
from paperroute.core.feeds import FeedKind
from paperroute.core.messages import (
    FetchFeed,
    Knob,
    KnobHeld,
    KnobPressed,
    KnobTurned,
    Render,
    Tick,
)
from paperroute.core.model import Pin
from paperroute.core.places import shown
from paperroute.core.saved import LiveView, Lookahead
from paperroute.core.state import step
from paperroute.core.state_model import State
from paperroute.core.state_views import saved_of
from paperroute.core.stop_view import DOT, StopView
from tests.core.alert_factories import (
    PENRITH_CENTRAL,
    WESTERN,
    XPLORER,
    sydney,
)
from tests.core.test_alert_state import (
    PARRAMATTA_CENTRAL,
    STRATHFIELD_TOWN_HALL,
    fetched,
    fresh_with,
    loaded_with,
    started,
)

TRAINS = 'v2/gtfs/alerts/sydneytrains'


def loaded(
    *,
    alerts: tuple[Alert, ...] = (XPLORER, WESTERN, PENRITH_CENTRAL),
    pins: tuple[Pin, ...] = (PARRAMATTA_CENTRAL,),
) -> State:
    """
    A state on the Stop view with some alerts fetched, none ahead cut.

    Parameters
    ----------
    alerts : tuple[Alert, ...]
        The trains feed's alerts.
    pins : tuple[Pin, ...]
        The pins.

    Returns
    -------
    State
        The state at `NOW`.
    """
    state = started(pins=pins)
    settings = replace(state.settings, alert_lookahead=Lookahead.ALL)
    state, _ = loaded_with(state=replace(state, settings=settings))
    return fresh_with(state=state, alerts=alerts)


def on_alerts(
    *,
    alerts: tuple[Alert, ...] = (XPLORER, WESTERN, PENRITH_CENTRAL),
    pins: tuple[Pin, ...] = (PARRAMATTA_CENTRAL,),
    selected: str | None = None,
    page: int = 0,
) -> State:
    """
    A state showing the Alerts view.

    Parameters
    ----------
    alerts : tuple[Alert, ...]
        The trains feed's alerts.
    pins : tuple[Pin, ...]
        The pins.
    selected : str | None
        Selected alert ID.
    page : int
        Page of its text.

    Returns
    -------
    State
        The state at `NOW`.
    """
    state = loaded(alerts=alerts, pins=pins)
    kept = replace(state.alerts, selected=selected, page=page)
    return replace(state, view=LiveView.ALERTS, alerts=kept)


def pressed_through(*, state: State, presses: int) -> list[LiveView]:
    """
    Press the bottom knob some times.

    Parameters
    ----------
    state : State
        State.
    presses : int
        Presses.

    Returns
    -------
    list[LiveView]
        The view after each press.
    """
    views = []
    for _ in range(presses):
        state, _ = step(state, KnobPressed(knob=Knob.BOTTOM))
        views.append(state.view)
    return views


def test_view_follows_strip_map_only_with_alerts() -> None:
    """Alerts follow Strip + map only while the pin has alerts listed."""
    assert pressed_through(state=loaded(), presses=5) == [
        LiveView.MAP,
        LiveView.STRIP,
        LiveView.STRIP_MAP,
        LiveView.ALERTS,
        LiveView.STOP,
    ]
    assert pressed_through(state=loaded(alerts=()), presses=4) == [
        LiveView.MAP,
        LiveView.STRIP,
        LiveView.STRIP_MAP,
        LiveView.STOP,
    ]


def test_top_turn_selects_and_resets_page() -> None:
    """A top turn moves the selection, clamped, back to page 0."""
    state = on_alerts(selected=XPLORER.alert_id, page=2)
    state, _ = step(state, KnobTurned(knob=Knob.TOP, steps=1))
    assert state.alerts.selected == WESTERN.alert_id
    assert state.alerts.page == 0
    state, _ = step(state, KnobTurned(knob=Knob.TOP, steps=2))
    assert state.alerts.selected == PENRITH_CENTRAL.alert_id
    state, _ = step(state, KnobTurned(knob=Knob.TOP, steps=-5))
    assert state.alerts.selected == XPLORER.alert_id


def test_bottom_turn_keeps_selection() -> None:
    """A bottom turn on Alerts changes nothing."""
    state = on_alerts(selected=WESTERN.alert_id, page=1)
    after, effects = step(state, KnobTurned(knob=Knob.BOTTOM, steps=2))
    assert after.alerts.selected == WESTERN.alert_id
    assert after.alerts.page == 1
    assert after.map.zoom == state.map.zoom
    assert after.view is LiveView.ALERTS
    assert not effects


def test_bottom_press_leaves_alerts() -> None:
    """A bottom press shows Stop; a hold pins or unpins as on Stop."""
    state = on_alerts()
    after, _ = step(state, KnobPressed(knob=Knob.BOTTOM))
    assert after.view is LiveView.STOP
    held, _ = step(state, KnobHeld(knob=Knob.BOTTOM))
    on_stop = replace(state, view=LiveView.STOP)
    held_on_stop, _ = step(on_stop, KnobHeld(knob=Knob.BOTTOM))
    assert held.places != state.places
    assert held.places == held_on_stop.places


def test_top_press_pages_not_chooser() -> None:
    """A top press on Alerts pages; on Stop it opens the chooser."""
    state = on_alerts()
    after, _ = step(state, KnobPressed(knob=Knob.TOP))
    assert after.chooser is None
    assert after.alerts.page == 1
    on_stop = replace(state, view=LiveView.STOP)
    after, _ = step(on_stop, KnobPressed(knob=Knob.TOP))
    assert after.chooser is not None


def test_top_turn_does_not_flip_pins() -> None:
    """With two pins, a top turn on Alerts keeps the shown pin."""
    state = on_alerts(pins=(PARRAMATTA_CENTRAL, STRATHFIELD_TOWN_HALL))
    after, _ = step(state, KnobTurned(knob=Knob.TOP, steps=1))
    assert shown(state=after) == shown(state=state)


def test_alerts_view_falls_back_when_empty() -> None:
    """When the only alert ends, the Alerts view becomes Stop."""
    state = on_alerts(alerts=(XPLORER,))
    later = sydney(day=24, hour=12, minute=30)
    state, effects = step(state, Tick(now=later))
    assert state.view is LiveView.STOP
    renders = [item for item in effects if isinstance(item, Render)]
    assert renders
    assert isinstance(renders[-1].view, StopView)


def test_selection_kept_by_id_after_refetch() -> None:
    """The selection follows its alert's ID, else the first listed."""
    state = on_alerts(
        alerts=(WESTERN, PENRITH_CENTRAL),
        selected=PENRITH_CENTRAL.alert_id,
    )
    alerts = (XPLORER, WESTERN, PENRITH_CENTRAL)
    state, _ = step(state, fetched(feed=TRAINS, alerts=alerts))
    assert state.alerts.selected == PENRITH_CENTRAL.alert_id
    state, _ = step(state, fetched(feed=TRAINS, alerts=(XPLORER, WESTERN)))
    assert state.alerts.selected == XPLORER.alert_id
    assert state.alerts.page == 0


def stop_view() -> StopView:
    """
    A Stop view to take the header from.

    Returns
    -------
    StopView
        The view.
    """
    return StopView(
        origin='Strathfield Station',
        destination='Town Hall Station',
        clock='12:00',
        hero=None,
        rows=(),
        banner=None,
        empty=None,
        scroll=0,
    )


def listed_of(
    *, alerts: tuple[Alert, ...], now: datetime,
) -> tuple[Listed, ...]:
    """
    List route alerts with no lookahead limit.

    Parameters
    ----------
    alerts : tuple[Alert, ...]
        The alerts.
    now : datetime
        Current time.

    Returns
    -------
    tuple[Listed, ...]
        What is listed.
    """
    return listed_alerts(
        matched=tuple(
            Matched(alert=item, tie=AlertTie.ROUTE, names_place=False)
            for item in alerts
        ),
        now=now,
        lookahead=None,
    )


def test_view_model_texts() -> None:
    """The row and full text carry the time copy."""
    now = sydney(day=26, hour=12)
    view = build_alerts_view(
        listed=listed_of(alerts=(PENRITH_CENTRAL,), now=now),
        selected=None,
        page=3,
        stop_view=stop_view(),
        now=now,
    )
    assert view.rows == (
        AlertRow(
            headline=PENRITH_CENTRAL.header,
            when=f'21:50 to Tue 1:30{DOT}+1 more',
            active=False,
        ),
    )
    assert view.selected == 0
    assert view.page == 3
    assert view.origin == 'Strathfield Station'
    assert view.text.title == PENRITH_CENTRAL.header
    assert view.text.body == PENRITH_CENTRAL.description
    assert view.text.when == f'21:50 to Tue 1:30{DOT}+1 more'
    assert view.text.also == ('Also Tue 21:50 to Wed 1:30',)


def test_rows_window_holds_selection() -> None:
    """Past `ALERT_ROWS` the rows scroll to keep the selection shown."""
    alerts = tuple(
        replace(WESTERN, alert_id=f'w{index}', header=f'{index}')
        for index in range(ALERT_ROWS + 2)
    )
    now = sydney(day=24, hour=12)
    view = build_alerts_view(
        listed=listed_of(alerts=alerts, now=now),
        selected='w7',
        page=0,
        stop_view=stop_view(),
        now=now,
    )
    assert len(view.rows) == ALERT_ROWS
    assert view.rows[view.selected].headline == '7'
    assert view.rows[0].headline == '2'

def test_alerts_view_not_saved() -> None:
    """The Alerts view is kept as the Stop view."""
    state = on_alerts()
    assert saved_of(state=state).view is LiveView.STOP


def test_no_positions_polled_on_alerts_view() -> None:
    """The Alerts view draws no vehicles, so polls no positions."""
    state = on_alerts()
    kinds = []
    for seconds in (5, 30, 60, 120):
        when = state.now + timedelta(seconds=seconds)
        state, effects = step(state, Tick(now=when))
        kinds += [
            effect.kind for effect in effects if isinstance(effect, FetchFeed)
        ]
    assert FeedKind.POSITIONS not in kinds
