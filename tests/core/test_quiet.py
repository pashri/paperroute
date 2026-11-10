"""Tests for sleeping through quiet hours and the five-minute wake."""

from collections.abc import Iterable
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from typing import Final

from paperroute_device.core.device_events import ConfirmAsked
from paperroute_device.core.device_model import (
    ConfirmAction,
    MenuRow,
    PowerAction,
    ResetKind,
    WifiPhase,
    WifiSetup,
    WifiStatus,
)
from paperroute_device.core.device_settings import QuietMode, Window
from paperroute_device.core.kit_effects import Render
from paperroute_device.core.kit_events import DeviceProbed, Tick
from paperroute_device.core.sleep import WAKE_FOR
from paperroute_device_testkit.device_fakes import device_facts
from paperroute_hardware.core.knob_events import (
    Knob,
    KnobPressed,
    KnobsHeld,
    KnobTurned,
)
from paperroute_sydney.city import SYDNEY_ZONE
from paperroute_testkit.core.factories import START, departure
from paperroute_testkit.core.steps import slept, step_answered, wakeful
from paperroute_transit.core.auto_model import AutoState
from paperroute_transit.core.messages import (
    DeparturesLoaded,
    Effect,
    Event,
    FetchFeed,
    LoadServiceSpan,
    ServiceSpanLoaded,
)
from paperroute_transit.core.model import AutoWindow, ServiceSpan, pin_ref
from paperroute_transit.core.panel_views import SleepView
from paperroute_transit.core.places import shown
from paperroute_transit.core.saved import Rate
from paperroute_transit.core.state import step
from paperroute_transit.core.state_model import Places, State
from paperroute_transit.core.stop_view import StopView
from tests.core.test_menu import DOWN, run, turn
from tests.core.test_state import BUSES, PINS, fresh
from tests.helpers import kit_step

DAY: Final[date] = START.date()
HALF_MINUTE: Final[timedelta] = timedelta(seconds=30)
BOTTOM: Final[KnobTurned] = KnobTurned(knob=Knob.BOTTOM, steps=1)
FRIDAY: Final[frozenset[int]] = frozenset({4})
EARLY: Final[AutoWindow] = AutoWindow(
    days=FRIDAY, start=time(5), end=time(6),
)


def at(hour: int, minute: int = 0, *, days: int = 0) -> datetime:
    """
    A Sydney time on the test day or a day after it.

    Parameters
    ----------
    hour : int
        Hour.
    minute : int
        Minute.
    days : int
        Days after the test day.

    Returns
    -------
    datetime
        Aware Sydney time.
    """
    return datetime.combine(
        DAY + timedelta(days=days), time(hour, minute), tzinfo=SYDNEY_ZONE,
    )


def boarded(*, state: State) -> State:
    """
    Load twenty bus departures, three minutes apart, from the state's time.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    State
        State with the departures loaded.
    """
    deps = tuple(
        departure(departs=state.now + timedelta(minutes=3 * (i + 1)))
        for i in range(20)
    )
    event = DeparturesLoaded(
        pin=shown(state=state),
        departures=deps,
        until=state.now + timedelta(hours=3),
    )
    return step(state, event)[0]


def evening(*, mode: QuietMode = QuietMode.MY_HOURS) -> State:
    """
    An awake state at 22:59 with departures loaded.

    Parameters
    ----------
    mode : QuietMode
        How quiet hours are chosen.

    Returns
    -------
    State
        State after a tick at 22:59.
    """
    state = fresh(now=at(22, 59))
    device = replace(state.settings.device, quiet_mode=mode)
    settings = replace(state.settings, device=device)
    state = boarded(state=replace(state, settings=settings))
    return step(state, Tick(now=at(22, 59)))[0]


def ticked(*, state: State, times: Iterable[datetime]) -> State:
    """
    Tick through several times.

    Parameters
    ----------
    state : State
        State.
    times : Iterable[datetime]
        Tick times, in order.

    Returns
    -------
    State
        State after the last tick.
    """
    for now in times:
        state = step(state, Tick(now=now))[0]
    return state


def asleep_at_one() -> State:
    """
    A state asleep in quiet hours at 01:00, with departures loaded.

    Returns
    -------
    State
        State after the 01:00 tick.
    """
    state = ticked(state=evening(), times=(at(23), at(0, 50, days=1)))
    return ticked(state=boarded(state=state), times=(at(1, days=1),))


def renders(*, effects: tuple[Effect, ...]) -> list[Render]:
    """
    List the Renders.

    Parameters
    ----------
    effects : tuple[Effect, ...]
        Effects.

    Returns
    -------
    list[Render]
        Each Render.
    """
    return [effect for effect in effects if isinstance(effect, Render)]


def fetches(*, effects: tuple[Effect, ...]) -> list[FetchFeed]:
    """
    List the FetchFeeds.

    Parameters
    ----------
    effects : tuple[Effect, ...]
        Effects.

    Returns
    -------
    list[FetchFeed]
        Each FetchFeed.
    """
    return [effect for effect in effects if isinstance(effect, FetchFeed)]


def span_loads(*, effects: tuple[Effect, ...]) -> list[LoadServiceSpan]:
    """
    List the LoadServiceSpans.

    Parameters
    ----------
    effects : tuple[Effect, ...]
        Effects.

    Returns
    -------
    list[LoadServiceSpan]
        Each LoadServiceSpan.
    """
    return [
        effect for effect in effects if isinstance(effect, LoadServiceSpan)
    ]


def test_my_hours_wrap_midnight() -> None:
    """The default hours sleep at 23:00 and wake at 05:30."""
    state = evening()
    assert not state.panel.asleep
    state, effects = step(state, Tick(now=at(23)))
    assert state.panel.asleep
    assert state.panel.by_quiet
    (render,) = renders(effects=effects)
    assert render.full
    assert isinstance(render.view, SleepView)
    state, effects = step(state, Tick(now=at(0, 10, days=1)))
    assert state.panel.asleep
    assert not fetches(effects=effects)
    state, effects = step_answered(
        state=state, event=Tick(now=at(5, 30, days=1)),
    )
    assert not state.panel.asleep
    assert isinstance(renders(effects=effects)[-1].view, StopView)


def test_equal_hours_never_sleep() -> None:
    """Quiet hours that start and end together never sleep."""
    state = evening()
    device = replace(state.settings.device, my_hours=Window(time(0), time(0)))
    settings = replace(state.settings, device=device)
    state = replace(state, settings=settings)
    for hour in (23, 0, 3, 5):
        state = step(state, Tick(now=at(hour, days=hour < 12)))[0]
        assert not state.panel.asleep


def test_first_touch_only_wakes() -> None:
    """A bottom turn wakes without scrolling; the next one scrolls."""
    state = asleep_at_one()
    scroll = state.scroll
    state, _ = step(state, BOTTOM)
    assert not state.panel.asleep
    assert state.scroll == scroll
    assert state.panel.awake_until == at(1, days=1) + WAKE_FOR
    state, _ = step(state, BOTTOM)
    assert state.scroll != scroll


def test_touch_keeps_awake_longer() -> None:
    """Each touch while awake moves the wake's end five minutes on."""
    state, _ = step(asleep_at_one(), BOTTOM)
    state = ticked(state=state, times=(at(1, 3, days=1),))
    state, _ = step(state, BOTTOM)
    assert state.panel.awake_until == at(1, 8, days=1)
    state = ticked(state=state, times=(at(1, 6, days=1),))
    assert not state.panel.asleep


def test_back_to_sleep_after_wake_for() -> None:
    """A wake in quiet hours lasts five minutes."""
    state, _ = step(asleep_at_one(), BOTTOM)
    state = ticked(state=state, times=(at(1, 4, days=1),))
    assert not state.panel.asleep
    state, effects = step(state, Tick(now=at(1, 5, days=1)))
    assert state.panel.asleep
    assert state.panel.by_quiet
    assert isinstance(renders(effects=effects)[0].view, SleepView)


def test_wake_near_end_stays_awake() -> None:
    """A wake that runs past the end of quiet hours stays awake."""
    state = ticked(state=asleep_at_one(), times=(at(5, 28, days=1),))
    state, _ = step(state, BOTTOM)
    state = ticked(state=state, times=(at(5, 30, days=1),))
    assert not state.panel.asleep
    state = ticked(state=state, times=(at(5, 33, days=1),))
    assert not state.panel.asleep
    assert state.panel.awake_until is None


def test_sleep_now_not_ended_by_tick() -> None:
    """Sleep now in the afternoon lasts until a touch."""
    state = boarded(state=fresh(now=at(14)))
    state, _ = step(state, Tick(now=at(14)))
    index = tuple(MenuRow).index(MenuRow.SLEEP)
    state, _ = run(state=state, events=(KnobsHeld(), turn(steps=index)))
    state, _ = step(state, DOWN)
    assert state.panel.asleep
    assert not state.panel.by_quiet
    state = ticked(
        state=state, times=(at(14, minute) for minute in range(1, 31)),
    )
    assert state.panel.asleep


def test_quiet_waits_for_open_menu() -> None:
    """An open menu holds off quiet sleep until it closes."""
    state, _ = step(evening(), KnobsHeld())
    state = ticked(state=state, times=(at(23),))
    assert state.panel.menu is not None
    assert not state.panel.asleep
    later = (at(23, minute) for minute in range(1, 6))
    state = ticked(state=state, times=later)
    assert state.panel.menu is None
    assert state.panel.asleep


def test_quiet_waits_for_phone_confirm() -> None:
    """A phone's confirm holds off quiet sleep."""
    state = ticked(state=evening(), times=(at(22, 59) + HALF_MINUTE,))
    event = ConfirmAsked(action=ConfirmAction.RESET_SETTINGS)
    state, _ = step(state, event)
    state = ticked(state=state, times=(at(23),))
    assert state.panel.confirm is not None
    assert not state.panel.asleep


def test_awake_polls_at_rate() -> None:
    """A wake polls at once, then at the off-peak rate."""
    state = asleep_at_one()
    board = replace(state.settings.board, offpeak_rate=Rate.SLOW)
    settings = replace(state.settings, board=board)
    state, effects = step(replace(state, settings=settings), DOWN)
    assert FetchFeed(feed=BUSES) in effects
    state, effects = step(state, Tick(now=at(1, 1, days=1)))
    assert FetchFeed(feed=BUSES) not in effects
    state, effects = step(state, Tick(now=at(1, 2, days=1)))
    assert FetchFeed(feed=BUSES) in effects


def followed(*, spans: dict[int, ServiceSpan | None]) -> State:
    """
    Follow the timetable at 22:59, with spans loaded.

    Parameters
    ----------
    spans : dict[int, ServiceSpan | None]
        Each span by days after the test day.

    Returns
    -------
    State
        State with the spans answered.
    """
    state = evening(mode=QuietMode.APP)
    for days, span in spans.items():
        event = ServiceSpanLoaded(
            pin=shown(state=state),
            day=DAY + timedelta(days=days),
            span=span,
        )
        state = step(state, event)[0]
    return state


def test_follow_asks_for_three_days() -> None:
    """Following the timetable asks for yesterday, today and tomorrow."""
    state = fresh(now=at(22, 59))
    device = replace(state.settings.device, quiet_mode=QuietMode.APP)
    settings = replace(state.settings, device=device)
    state, effects = step(
        replace(state, settings=settings), Tick(now=at(22, 59)),
    )
    days = [load.day for load in span_loads(effects=effects)]
    assert days == [DAY + timedelta(days=d) for d in (-1, 0, 1)]
    _, effects = step(state, Tick(now=at(23)))
    assert not span_loads(effects=effects)


def test_follow_sleeps_between_spans() -> None:
    """Asleep from 30 min after the last trip to 30 min before the first."""
    state = followed(spans={
        -1: None,
        0: ServiceSpan(first_start=at(5), last_end=at(0, 40, days=1)),
        1: ServiceSpan(
            first_start=at(5, 10, days=1), last_end=at(0, 40, days=2),
        ),
        2: None,
    })
    state = ticked(state=state, times=(at(23), at(1, 9, days=1)))
    assert not state.panel.asleep
    state = ticked(state=state, times=(at(1, 10, days=1),))
    assert state.panel.asleep
    state = ticked(state=state, times=(at(4, 39, days=1),))
    assert state.panel.asleep
    state = ticked(state=state, times=(at(4, 40, days=1),))
    assert not state.panel.asleep


def test_follow_no_trips_next_day_stays_awake() -> None:
    """With no trips the next day, the night stays awake."""
    state = followed(spans={
        -1: None,
        0: ServiceSpan(first_start=at(5), last_end=at(0, 40, days=1)),
        1: None,
    })
    for hour in (23, 1, 3, 5):
        state = step(state, Tick(now=at(hour, days=hour < 12)))[0]
        assert not state.panel.asleep


def test_follow_reloads_on_pin_change() -> None:
    """A top turn to another pin forgets the spans and asks again."""
    state = evening(mode=QuietMode.APP)
    state = replace(state, places=Places(pins=PINS))
    state, _ = step(state, Tick(now=at(23)))
    for days in (-1, 0, 1):
        event = ServiceSpanLoaded(
            pin=PINS[0], day=DAY + timedelta(days=days), span=None,
        )
        state = step(state, event)[0]
    assert len(state.calendar.spans) == 3
    state, _ = step(state, KnobTurned(knob=Knob.TOP, steps=1))
    state, effects = step(state, Tick(now=at(23, 1)))
    loads = span_loads(effects=effects)
    assert [load.pin for load in loads] == [PINS[1]] * 3
    assert not state.calendar.spans


def test_stale_span_is_ignored() -> None:
    """A span for a pin no longer shown is dropped."""
    state = evening(mode=QuietMode.APP)
    event: Event = ServiceSpanLoaded(pin=PINS[2], day=DAY, span=None)
    after, _ = step(state, event)
    assert after.calendar.spans == state.calendar.spans


def test_quiet_waits_for_power() -> None:
    """A power action under way holds off quiet sleep."""
    state = replace(evening(), power=PowerAction.OFF)
    state = ticked(state=state, times=(at(23),))
    assert not state.panel.asleep


def test_quiet_wakes_from_a_tick_only_when_by_quiet() -> None:
    """A quiet sleep ends at the end of the hours with a fetch."""
    state = ticked(state=asleep_at_one(), times=(at(5, 29, days=1),))
    state, effects = step_answered(
        state=state, event=Tick(now=at(5, 30, days=1)),
    )
    assert not state.panel.asleep
    assert FetchFeed(feed=BUSES) in effects
    assert len(renders(effects=effects)) == 1


def test_quiet_waits_for_chooser() -> None:
    """An open route chooser holds off quiet sleep."""
    state, _ = step(evening(), KnobPressed(knob=Knob.TOP))
    assert state.chooser is not None
    state = ticked(state=state, times=(at(23),))
    assert not state.panel.asleep


def test_quiet_waits_for_wifi_setup() -> None:
    """Wi-Fi setup open on the panel holds off quiet sleep."""
    state = evening()
    panel = replace(state.panel, wifi=WifiSetup(phase=WifiPhase.SCANNING))
    state = ticked(state=replace(state, panel=panel), times=(at(23),))
    assert not state.panel.asleep


def test_quiet_waits_for_reset() -> None:
    """A reset under way holds off quiet sleep."""
    state = evening()
    panel = replace(state.panel, resetting=ResetKind.SETTINGS)
    state = ticked(state=replace(state, panel=panel), times=(at(23),))
    assert not state.panel.asleep


def test_sleep_now_woken_in_quiet_hours_sleeps_again() -> None:
    """A touch ending a Sleep now in quiet hours wakes for five minutes."""
    state = evening()
    index = tuple(MenuRow).index(MenuRow.SLEEP)
    state, _ = run(state=state, events=(KnobsHeld(), turn(steps=index)))
    state, _ = step(state, DOWN)
    assert not state.panel.by_quiet
    state = ticked(state=state, times=(at(23),))
    state, _ = step(state, BOTTOM)
    assert not state.panel.asleep
    state = ticked(state=state, times=(at(23, 4),))
    assert not state.panel.asleep
    state = ticked(state=state, times=(at(23, 5),))
    assert state.panel.asleep
    assert state.panel.by_quiet


def windowed(
    *,
    now: datetime,
    window: AutoWindow = EARLY,
    mode: QuietMode = QuietMode.MY_HOURS,
) -> State:
    """
    A trusted state whose second pin has a window, after a tick.

    Parameters
    ----------
    now : datetime
        Time of the state and its first tick.
    window : AutoWindow
        The second pin's window.
    mode : QuietMode
        How quiet hours are chosen.

    Returns
    -------
    State
        State showing the first pin, with departures loaded.
    """
    state = fresh(now=now)
    places = Places(pins=(PINS[0], replace(PINS[1], windows=(window,))))
    state = replace(
        state,
        places=places,
        panel=replace(state.panel, facts=device_facts()),
        settings=replace(
            state.settings,
            device=replace(state.settings.device, quiet_mode=mode),
        ),
    )
    return step(boarded(state=state), Tick(now=now))[0]


def shows_work(*, state: State) -> bool:
    """
    Say whether the windowed pin is shown.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    bool
        True when the second pin is shown.
    """
    return pin_ref(pin=shown(state=state)) == pin_ref(pin=PINS[1])


def slept_now(*, state: State) -> State:
    """
    Choose Sleep now from the Settings menu.

    Parameters
    ----------
    state : State
        State, awake.

    Returns
    -------
    State
        State asleep, not by quiet hours.
    """
    index = tuple(MenuRow).index(MenuRow.SLEEP)
    state, _ = run(state=state, events=(KnobsHeld(), turn(steps=index)))
    return step(state, DOWN)[0]


def test_window_wakes_quiet_sleep() -> None:
    """A window's start wakes a quiet sleep, which does not come back."""
    state = ticked(state=windowed(now=at(4, 30)), times=(at(4, 59),))
    assert state.panel.asleep
    assert state.panel.by_quiet
    state = ticked(state=state, times=(at(5),))
    assert not state.panel.asleep
    assert shows_work(state=state)
    state = boarded(state=state)
    state, effects = step(state, Tick(now=at(5, 2)))
    assert not state.panel.asleep
    assert FetchFeed(feed=BUSES) in fetches(effects=effects)


def test_window_wakes_sleep_now() -> None:
    """A window's start wakes a Sleep now sleep."""
    state = ticked(state=windowed(now=at(4, 30)), times=(at(4, 59),))
    state = slept(state=state, by_quiet=False)
    state = ticked(state=state, times=(at(5),))
    assert not state.panel.asleep
    assert shows_work(state=state)


def test_sleep_now_in_window_lasts() -> None:
    """Sleep now in a window lasts until a touch."""
    state = ticked(state=windowed(now=at(4, 30)), times=(at(5), at(5, 10)))
    state = slept_now(state=state)
    assert state.panel.asleep
    later = (at(5, minute) for minute in range(11, 60))
    state = ticked(state=state, times=later)
    assert state.panel.asleep
    state, _ = step(state, BOTTOM)
    assert not state.panel.asleep


def test_window_end_in_quiet_hours_sleeps() -> None:
    """A window ending in quiet hours goes back and sleeps, drawn once."""
    late = AutoWindow(days=FRIDAY, start=time(23), end=time(23, 30))
    state = windowed(now=at(22, 59), window=late)
    state = ticked(
        state=state, times=(at(23, minute) for minute in range(30)),
    )
    assert not state.panel.asleep
    assert shows_work(state=state)
    state, effects = step(state, Tick(now=at(23, 30)))
    assert state.panel.asleep
    assert not shows_work(state=state)
    assert state.auto == AutoState()
    drawn = renders(effects=effects)
    assert len(drawn) == 1
    assert isinstance(drawn[0].view, SleepView)


def night_spans(*, state: State) -> State:
    """
    Load spans that say the shown pin's service is over.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    State
        State with yesterday's and today's spans, asleep from 01:10 to
        06:30.
    """
    spans = {
        -1: ServiceSpan(first_start=at(5, days=-1), last_end=at(0, 40)),
        0: ServiceSpan(first_start=at(7), last_end=at(0, 40, days=1)),
    }
    for days, span in spans.items():
        event = ServiceSpanLoaded(
            pin=shown(state=state), day=DAY + timedelta(days=days), span=span,
        )
        state = step(state, event)[0]
    return state


def test_follow_timetable_overridden_too() -> None:
    """A window's start wakes a sleep by the timetable too."""
    state = windowed(now=at(4, 30), mode=QuietMode.APP)
    state = ticked(state=night_spans(state=state), times=(at(4, 59),))
    assert state.panel.asleep
    state = ticked(state=state, times=(at(5),))
    assert not state.panel.asleep
    state = night_spans(state=boarded(state=state))
    state = ticked(state=state, times=(at(5) + HALF_MINUTE, at(5, 20)))
    assert not state.panel.asleep


def test_overridden_window_still_awake() -> None:
    """An override leaves the panel awake until the window ends."""
    state = ticked(state=windowed(now=at(4, 30)), times=(at(5), at(5, 5)))
    state, _ = step(state, KnobTurned(knob=Knob.TOP, steps=1))
    auto = replace(state.auto, overridden_at=at(5, 5))
    state = replace(state, auto=auto)
    for minute in range(6, 30):
        state = ticked(state=state, times=(at(5, minute),))
        assert not state.panel.asleep


def test_quiet_sleep_tick_fetches_nothing() -> None:
    """The tick that sleeps the screen fetches nothing and counts no call."""
    state = boarded(state=fresh(now=at(22, 59)))
    state, _ = step_answered(state=state, event=Tick(now=at(22, 59)))
    tick = Tick(now=at(23))
    _, control = kit_step(state=wakeful(state=state), event=tick)
    assert fetches(effects=control)
    after, effects = kit_step(state=state, event=tick)
    assert after.panel.asleep
    assert not fetches(effects=effects)
    assert after.quota == state.quota


def test_offline_clears_the_poll_clocks() -> None:
    """Going offline through a probe forgets when the feeds were fetched."""
    state = boarded(state=fresh())
    state, _ = step_answered(state=state, event=Tick(now=START))
    assert state.last_fetch is not None
    facts = replace(device_facts(), wifi=WifiStatus(managed=True))
    state, _ = kit_step(state=state, event=DeviceProbed(facts=facts))
    assert state.last_fetch is None
    assert not state.map.fetched_at
