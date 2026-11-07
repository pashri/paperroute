"""Tests for the auto pin rules: start, chain, end, wait and trust."""

from collections.abc import Iterable
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from typing import Final

from paperroute.core.alert_model import AlertTie, Matched, Period
from paperroute.core.auto_model import AutoState, Before, Occurrence
from paperroute.core.auto_pins import rule_holds
from paperroute.core.messages import (
    DeparturesLoaded,
    Effect,
    LoadDepartures,
    PinDeleted,
    StripLoaded,
    Tick,
)
from paperroute.core.model import SYDNEY, AutoWindow, Pin, pin_ref
from paperroute.core.places import shown
from paperroute.core.saved import LiveView, Saved, seeded
from paperroute.core.state import initial_state, step
from paperroute.core.state_model import Places, State
from paperroute_hardware.core.knob_events import (
    Knob,
    KnobHeld,
    KnobPressed,
    KnobTurned,
)
from paperroute_testkit.core.alert_factories import alert
from paperroute_testkit.core.factories import START, departure, pin_at
from paperroute_testkit.core.steps import wakeful
from paperroute_testkit.fakes import device_facts
from tests.core.chooser_walks import CHOSEN, CHOSEN_KEY, NEXT, REACH, TO_STAGE
from tests.core.test_chooser_steps import picked, run_events

DAY: Final[date] = START.date()
WEEKDAYS: Final[frozenset[int]] = frozenset(range(5))
HOME: Final[Pin] = pin_at('home', x=100.0)
OTHER: Final[Pin] = pin_at('other', x=300.0)
OPEN: Final[KnobPressed] = KnobPressed(knob=Knob.TOP)
TOP_BACK: Final[KnobTurned] = KnobTurned(knob=Knob.TOP, steps=-1)
BOTTOM_TURN: Final[KnobTurned] = KnobTurned(knob=Knob.BOTTOM, steps=1)


def at(
    hour: int, minute: int = 0, second: int = 0, *, days: int = 0,
) -> datetime:
    """
    A Sydney time on the test day, a Friday, or a day after it.

    Parameters
    ----------
    hour : int
        Hour.
    minute : int
        Minute.
    second : int
        Second.
    days : int
        Days after the test day.

    Returns
    -------
    datetime
        Aware Sydney time.
    """
    return datetime.combine(
        DAY + timedelta(days=days),
        time(hour, minute, second),
        tzinfo=SYDNEY,
    )


def windowed(
    *, name: str, x: float, start: time, end: time,
) -> Pin:
    """
    A pin with one weekday window.

    Parameters
    ----------
    name : str
        From stop ID and name.
    x : float
        Easting in metres.
    start : time
        Window start.
    end : time
        Window end.

    Returns
    -------
    Pin
        The pin.
    """
    window = AutoWindow(days=WEEKDAYS, start=start, end=end)
    return replace(pin_at(name, x=x), windows=(window,))


WORK: Final[Pin] = windowed(
    name='work', x=200.0, start=time(7, 30), end=time(9),
)


def trusted(*, state: State, synced: bool | None = True) -> State:
    """
    Give a state device facts and quiet hours that never come.

    Parameters
    ----------
    state : State
        State.
    synced : bool | None
        Whether the clock is synced.

    Returns
    -------
    State
        State with the facts.
    """
    facts = replace(device_facts(), clock_synced=synced)
    state = replace(state, panel=replace(state.panel, facts=facts))
    return wakeful(state=state)


def started(
    *,
    pins: tuple[Pin, ...] = (HOME, WORK),
    current: int = 0,
    now: datetime | None = None,
) -> State:
    """
    A trusted state with some pins and departures loaded.

    Parameters
    ----------
    pins : tuple[Pin, ...]
        Pins.
    current : int
        Current pin.
    now : datetime | None
        Start time; 07:29 when None.

    Returns
    -------
    State
        State after a first tick with the shown place's departures.
    """
    when = at(7, 29) if now is None else now
    saved = replace(seeded(pin=pins[0]), pins=pins, current=current)
    state = trusted(state=initial_state(saved=saved, now=when, stored=True))
    state = step(state, Tick(now=when))[0]
    return boarded(state=state)


def boarded(*, state: State) -> State:
    """
    Load departures for the shown place.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    State
        State with departures until six hours on.
    """
    event = DeparturesLoaded(
        pin=shown(state=state),
        departures=(departure(departs=state.now + timedelta(minutes=3)),),
        until=state.now + timedelta(hours=6),
    )
    return step(state, event)[0]


def ticked(*, state: State, times: Iterable[datetime]) -> State:
    """
    Tick through several times.

    Parameters
    ----------
    state : State
        State.
    times : Iterable[datetime]
        Times.

    Returns
    -------
    State
        State after the last tick.
    """
    for now in times:
        state = step(state, Tick(now=now))[0]
    return state


def tick(*, state: State, now: datetime) -> tuple[State, tuple[Effect, ...]]:
    """
    Tick once.

    Parameters
    ----------
    state : State
        State.
    now : datetime
        Time.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        State and effects.
    """
    return step(state, Tick(now=now))


def occurrence(*, pin: Pin, start: datetime, end: datetime) -> Occurrence:
    """
    An occurrence of a pin's window.

    Parameters
    ----------
    pin : Pin
        Pin.
    start : datetime
        Start.
    end : datetime
        End.

    Returns
    -------
    Occurrence
        The occurrence.
    """
    return Occurrence(ref=pin_ref(pin=pin), start=start, end=end)


WORK_TODAY: Final[Occurrence] = occurrence(
    pin=WORK, start=at(7, 30), end=at(9),
)


def is_shown(*, state: State, pin: Pin) -> bool:
    """
    Say whether a pin is shown, pinned.

    Parameters
    ----------
    state : State
        State.
    pin : Pin
        Pin.

    Returns
    -------
    bool
        True when the pin is the current pin and nothing is unpinned.
    """
    return state.places.unpinned is None and pin_ref(
        pin=shown(state=state),
    ) == pin_ref(pin=pin)


def loads(*, effects: tuple[Effect, ...]) -> list[LoadDepartures]:
    """
    Pick out the departure loads.

    Parameters
    ----------
    effects : tuple[Effect, ...]
        Effects.

    Returns
    -------
    list[LoadDepartures]
        The loads.
    """
    return [effect for effect in effects if isinstance(effect, LoadDepartures)]


def test_window_start_shows_pin() -> None:
    """A window's start shows its pin and remembers the place before."""
    state = replace(started(), view=LiveView.MAP)
    state, effects = tick(state=state, now=at(7, 30))
    assert is_shown(state=state, pin=WORK)
    assert state.auto.occurrence == WORK_TODAY
    assert state.auto.before == Before(place=HOME, pinned=True)
    assert [load.pin for load in loads(effects=effects)] == [WORK]
    assert state.view is LiveView.MAP


def test_window_end_returns() -> None:
    """A window's end shows the place from before."""
    state = ticked(state=started(), times=(at(7, 30), at(9)))
    assert is_shown(state=state, pin=HOME)
    assert state.auto == AutoState()


def test_already_showing_counts_as_auto() -> None:
    """A pin already shown at its start is recorded but nothing moves."""
    state, effects = tick(state=started(current=1), now=at(7, 30))
    assert state.auto.occurrence == WORK_TODAY
    assert state.auto.before is not None
    assert pin_ref(pin=state.auto.before.place) == pin_ref(pin=WORK)
    assert not loads(effects=effects)
    state = ticked(state=state, times=(at(9),))
    assert is_shown(state=state, pin=WORK)


def test_unpinned_before_restored() -> None:
    """An unpinned place browsed at the start is shown again at the end."""
    state = started()
    places = replace(state.places, unpinned=OTHER)
    state = ticked(state=replace(state, places=places), times=(at(7, 30),))
    assert state.auto.before == Before(place=OTHER, pinned=False)
    assert is_shown(state=state, pin=WORK)
    state = ticked(state=state, times=(at(9),))
    assert state.places.unpinned == OTHER


def test_chain_returns_to_first_before() -> None:
    """Back-to-back windows keep the first place to go back to."""
    work = windowed(name='work', x=200.0, start=time(7, 30), end=time(8))
    gym = windowed(name='gym', x=400.0, start=time(8), end=time(8, 30))
    state = started(pins=(HOME, work, gym))
    shown_at = []
    for now in (at(7, 30), at(8), at(8, 30)):
        state = ticked(state=state, times=(now,))
        shown_at.append(pin_ref(pin=shown(state=state)))
        if state.auto.before is not None:
            assert state.auto.before.place == HOME
    assert shown_at == [
        pin_ref(pin=work), pin_ref(pin=gym), pin_ref(pin=HOME),
    ]


def test_overlap_latest_wins_then_resumes() -> None:
    """The latest start wins; the longer window resumes after it."""
    work = windowed(name='work', x=200.0, start=time(7), end=time(10))
    gym = windowed(name='gym', x=400.0, start=time(8), end=time(8, 30))
    state = started(pins=(HOME, work, gym), now=at(6, 59))
    state = ticked(state=state, times=(at(7), at(8, 15)))
    assert is_shown(state=state, pin=gym)
    state = ticked(state=state, times=(at(8, 35),))
    assert is_shown(state=state, pin=work)
    state = ticked(state=state, times=(at(10),))
    assert is_shown(state=state, pin=HOME)


def test_start_waits_for_chooser() -> None:
    """A start waits while the chooser is open."""
    state = step(started(), OPEN)[0]
    assert state.chooser is not None
    state = ticked(state=state, times=(at(7, 30),))
    assert is_shown(state=state, pin=HOME)
    assert state.auto == AutoState()
    state = ticked(state=state, times=(at(7, 32),))
    state = step(state, OPEN)[0]
    assert state.chooser is None
    state = ticked(state=state, times=(at(7, 32, 30),))
    assert is_shown(state=state, pin=WORK)


def test_untrusted_clock_does_nothing() -> None:
    """The rules wait for a trusted clock, or none to check."""
    base = started()
    for synced in (False, None):
        state = trusted(state=base, synced=synced)
        state = ticked(state=state, times=(at(7, 30),))
        assert is_shown(state=state, pin=WORK) is (synced is None)
    no_facts = replace(base, panel=replace(base.panel, facts=None))
    state = ticked(state=no_facts, times=(at(7, 30),))
    assert is_shown(state=state, pin=HOME)
    assert state.auto == AutoState()


def restarted(*, auto: AutoState, current: int) -> State:
    """
    Start from a saved state with an auto block.

    Parameters
    ----------
    auto : AutoState
        Saved auto state.
    current : int
        Current pin.

    Returns
    -------
    State
        Trusted state at 08:00.
    """
    saved = Saved(
        pins=(HOME, WORK),
        current=current,
        unpinned=None,
        view=LiveView.STOP,
        auto=auto,
    )
    state = initial_state(saved=saved, now=at(8), stored=True)
    return trusted(state=state)


def test_restart_mid_window_returns_to_before() -> None:
    """A restart mid-window keeps the occurrence and goes back at its end."""
    auto = AutoState(
        occurrence=WORK_TODAY, before=Before(place=HOME, pinned=True),
    )
    state = ticked(state=restarted(auto=auto, current=1), times=(at(8),))
    assert is_shown(state=state, pin=WORK)
    assert state.auto == auto
    state = ticked(state=state, times=(at(9),))
    assert is_shown(state=state, pin=HOME)


def test_restart_mid_window_keeps_override() -> None:
    """A restart after an override stays where it is."""
    auto = AutoState(
        occurrence=WORK_TODAY,
        before=Before(place=HOME, pinned=True),
        overridden_at=at(7, 45),
    )
    state = ticked(state=restarted(auto=auto, current=0), times=(at(8),))
    assert is_shown(state=state, pin=HOME)
    state = ticked(state=state, times=(at(9),))
    assert is_shown(state=state, pin=HOME)


def test_boot_mid_window_applies() -> None:
    """Booting mid-window shows the pin on the first trusted tick."""
    state = ticked(
        state=restarted(auto=AutoState(), current=0), times=(at(8),),
    )
    assert is_shown(state=state, pin=WORK)


def test_before_pin_deleted_stays() -> None:
    """With the place before deleted, the end leaves the pin shown."""
    state = ticked(state=started(), times=(at(7, 30),))
    state = step(state, PinDeleted(ref=pin_ref(pin=HOME)))[0]
    state = ticked(state=state, times=(at(9),))
    assert is_shown(state=state, pin=WORK)
    assert state.auto == AutoState()


def home_alerted(*, state: State) -> State:
    """
    Tie one running alert to `HOME`.

    Parameters
    ----------
    state : State
        State showing `HOME`.

    Returns
    -------
    State
        State with the alert matched.
    """
    found = alert(
        alert_id='home',
        header='Home lifts out',
        periods=(Period(start=at(6), end=at(12)),),
        stops=frozenset({'home'}),
    )
    matched = Matched(alert=found, tie=AlertTie.STOP, names_place=True)
    alerts = replace(state.alerts, matched=(HOME, (matched,)))
    return replace(state, alerts=alerts)


def test_alerts_view_falls_back() -> None:
    """The Alerts view becomes the Stop view; other views are kept."""
    state = home_alerted(state=started())
    state = replace(state, view=LiveView.ALERTS)
    state = ticked(state=state, times=(at(7, 29, 30),))
    assert state.view is LiveView.ALERTS
    state = ticked(state=state, times=(at(7, 30),))
    assert is_shown(state=state, pin=WORK)
    assert state.view is LiveView.STOP
    strip = ticked(
        state=replace(started(), view=LiveView.STRIP), times=(at(7, 30),),
    )
    assert strip.view is LiveView.STRIP


def test_places_unchanged_without_windows() -> None:
    """Pins with no windows are never moved."""
    state = started(pins=(HOME, OTHER))
    after = ticked(state=state, times=(at(7, 30),))
    assert after.places == Places(pins=(HOME, OTHER), current=0)
    assert after.auto == AutoState()


def in_window(*, pins: tuple[Pin, ...] = (HOME, WORK)) -> State:
    """
    A state with `WORK` shown by the rules at 07:30.

    Parameters
    ----------
    pins : tuple[Pin, ...]
        Pins.

    Returns
    -------
    State
        State after the 07:30 tick.
    """
    return ticked(state=started(pins=pins), times=(at(7, 30),))


def test_top_turn_overrides() -> None:
    """A top turn away overrides the window until its next start."""
    state = step(in_window(), TOP_BACK)[0]
    assert is_shown(state=state, pin=HOME)
    assert state.auto.overridden_at == at(7, 30)
    state = ticked(state=state, times=(at(9),))
    assert is_shown(state=state, pin=HOME)
    assert state.auto == AutoState()
    state = ticked(state=state, times=(at(7, 30, days=3),))
    assert is_shown(state=state, pin=WORK)
    assert state.auto.overridden_at is None


def test_unpin_auto_pin_overrides() -> None:
    """Unpinning the auto pin with a bottom hold overrides it."""
    state = step(in_window(), KnobHeld(knob=Knob.BOTTOM))[0]
    assert state.places.unpinned is not None
    assert state.auto.overridden_at == at(7, 30)


def test_view_scroll_chooser_do_not_override() -> None:
    """Views, scrolling and the chooser are not overrides."""
    state = in_window()
    for event in (KnobPressed(knob=Knob.BOTTOM), BOTTOM_TURN):
        state = step(state, event)[0]
        assert state.auto.overridden_at is None
    state, _ = run_events(
        state=state,
        events=[
            *TO_STAGE,
            StripLoaded(key=CHOSEN_KEY, reach=REACH),
            picked(),
            NEXT,
        ],
    )
    assert state.chooser is None
    assert state.places.unpinned == CHOSEN
    assert state.auto.overridden_at is None
    state = ticked(state=state, times=(at(9),))
    assert is_shown(state=state, pin=HOME)


def test_alerts_top_turn_not_override() -> None:
    """A top turn on the Alerts view moves the selection only."""
    state = home_alerted(state=started(current=1))
    state = ticked(state=state, times=(at(7, 30),))
    matched = state.alerts.matched
    assert matched is not None
    alerts = replace(state.alerts, matched=(WORK, matched[1]))
    state = replace(state, alerts=alerts, view=LiveView.ALERTS)
    state = step(state, TOP_BACK)[0]
    assert state.view is LiveView.ALERTS
    assert is_shown(state=state, pin=WORK)
    assert state.auto.overridden_at is None


def test_override_then_chain_reapplies() -> None:
    """A later window still applies after an override, then stays."""
    work = windowed(name='work', x=200.0, start=time(7, 30), end=time(8))
    gym = windowed(name='gym', x=400.0, start=time(8), end=time(8, 30))
    state = ticked(state=in_window(pins=(HOME, work, gym)), times=(at(7, 40),))
    state = step(state, TOP_BACK)[0]
    assert state.auto.overridden_at == at(7, 40)
    state = ticked(state=state, times=(at(8),))
    assert is_shown(state=state, pin=gym)
    state = ticked(state=state, times=(at(8, 30),))
    assert is_shown(state=state, pin=HOME)


def test_override_not_undone_by_older_window() -> None:
    """An older window resuming after an override takes nothing back."""
    work = windowed(name='work', x=200.0, start=time(7), end=time(10))
    gym = windowed(name='gym', x=400.0, start=time(8), end=time(8, 30))
    state = started(pins=(HOME, work, gym), now=at(6, 59))
    state = ticked(state=state, times=(at(7), at(7, 45)))
    state = step(state, TOP_BACK)[0]
    assert is_shown(state=state, pin=HOME)
    state = ticked(state=state, times=(at(8),))
    assert is_shown(state=state, pin=gym)
    assert rule_holds(state=state)
    state = ticked(state=state, times=(at(8, 30),))
    assert is_shown(state=state, pin=HOME)
    assert state.auto.overridden_at == at(7, 45)
    assert state.auto.occurrence is not None
    assert state.auto.occurrence.ref == pin_ref(pin=work)
    state = ticked(state=state, times=(at(10),))
    assert is_shown(state=state, pin=HOME)


def test_window_after_override_returns_to_shown() -> None:
    """A window after an override goes back to the place shown at its start."""
    work = windowed(name='work', x=200.0, start=time(7), end=time(10))
    gym = windowed(name='gym', x=400.0, start=time(8), end=time(8, 30))
    state = started(pins=(OTHER, HOME, work, gym), now=at(6, 59))
    state = ticked(state=state, times=(at(7), at(7, 45)))
    state = step(state, TOP_BACK)[0]
    assert is_shown(state=state, pin=HOME)
    state = ticked(state=state, times=(at(8), at(8, 30)))
    assert is_shown(state=state, pin=HOME)


def test_turn_outside_window_noop() -> None:
    """A top turn with nothing recorded is no override."""
    state = step(started(), KnobTurned(knob=Knob.TOP, steps=1))[0]
    assert is_shown(state=state, pin=WORK)
    assert state.auto == AutoState()


def test_rule_holds_follows_rule() -> None:
    """The rule holds only while it shows its pin, pinned and unturned."""
    assert rule_holds(state=in_window())
    assert not rule_holds(state=started())
    assert not rule_holds(state=step(in_window(), TOP_BACK)[0])
    assert not rule_holds(
        state=ticked(state=in_window(), times=(at(9),)),
    )
    unpinned = step(in_window(), KnobHeld(knob=Knob.BOTTOM))[0]
    assert not rule_holds(state=unpinned)
    plain = replace(unpinned, auto=replace(unpinned.auto, overridden_at=None))
    assert not rule_holds(state=plain)
