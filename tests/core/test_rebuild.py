"""Tests for when an outdated timetable is rebuilt."""

from collections.abc import Sequence
from dataclasses import replace
from datetime import timedelta

import pytest

from paperroute.core.device_model import (
    MENU_ROWS,
    Menu,
    MenuRow,
    PanelScreen,
    WifiStatus,
)
from paperroute.core.map_view import DEFAULT_ZOOM
from paperroute.core.messages import (
    ApiKeyChanged,
    ColumnLoaded,
    DeviceProbed,
    Effect,
    Event,
    FetchFeed,
    Knob,
    KnobPressed,
    KnobsHeld,
    KnobTurned,
    LoadColumn,
    NoticeView,
    RebuildTimetable,
    Render,
    SettingsChanged,
    Tick,
    TimetableRebuilt,
    TimetableSeen,
)
from paperroute.core.panel_screens import device_rows
from paperroute.core.panel_views import MenuView
from paperroute.core.rebuild import (
    BACKOFF_MAX,
    building_view,
    forced,
    is_outdated,
)
from paperroute.core.saved import LiveView, Saved
from paperroute.core.state import initial_state, step
from paperroute.core.state_model import Rebuild, State
from tests.core.chooser_walks import DOWN, KINDS, NEXT, OPEN
from tests.core.factories import START, pin_at
from tests.core.steps import step_untouched
from tests.fakes import device_facts

OLD = TimetableSeen(version='v', schema='4')
CURRENT = TimetableSeen(version='v', schema='7')
MISSING = TimetableSeen(version=None)


def pinned() -> State:
    """
    A state with one pin, drawn once.

    Returns
    -------
    State
        The state after its first tick.
    """
    saved = Saved(
        pins=(pin_at('first', x=100.0),),
        current=0,
        unpinned=None,
        view=LiveView.STOP,
        zoom=DEFAULT_ZOOM,
    )
    state = initial_state(saved=saved, now=START, stored=True)
    return step(state, Tick(now=START))[0]


def rebuilds(*, effects: Sequence[Effect]) -> int:
    """
    Count the RebuildTimetable effects.

    Parameters
    ----------
    effects : Sequence[Effect]
        Effects.

    Returns
    -------
    int
        How many ask for a rebuild.
    """
    return sum(isinstance(effect, RebuildTimetable) for effect in effects)


def run_events(
    *, state: State, events: Sequence[Event]
) -> tuple[State, list[Effect]]:
    """
    Apply events in turn, gathering every effect.

    Parameters
    ----------
    state : State
        State before.
    events : Sequence[Event]
        Events.

    Returns
    -------
    tuple[State, list[Effect]]
        The state after and all effects.
    """
    gathered: list[Effect] = []
    for event in events:
        state, effects = step(state, event)
        gathered.extend(effects)
    return state, gathered


def failed_at(*, minutes: int) -> State:
    """
    Start a rebuild at START and fail it some minutes later.

    Parameters
    ----------
    minutes : int
        Minutes after START the failure arrives.

    Returns
    -------
    State
        The state after the failure.
    """
    later = START + timedelta(minutes=minutes)
    state, _ = run_events(
        state=pinned(),
        events=[OLD, Tick(now=later), TimetableRebuilt(ok=False)],
    )
    return state


@pytest.mark.parametrize(
    ('schema', 'expected'),
    [('4', True), ('5', True), ('6', True), ('7', False), ('8', False),
     (None, False), ('x', False)],
)
def test_is_outdated(schema: str | None, expected: bool) -> None:
    """Only a whole-number schema below the app's is outdated."""
    assert is_outdated(schema=schema) is expected


def test_outdated_timetable_starts_rebuild() -> None:
    """A schema-4 file asks for one rebuild and marks it running."""
    state, effects = step(pinned(), OLD)
    assert rebuilds(effects=effects) == 1
    assert state.rebuild.running and state.rebuild.outdated


def test_switch_off_stops_rebuild() -> None:
    """With the switch off, an outdated, due timetable is left alone."""
    _, effects = run_events(
        state=pinned(),
        events=[
            SettingsChanged(auto_rebuild=False),
            OLD,
            Tick(now=START + timedelta(hours=1)),
        ],
    )
    assert rebuilds(effects=effects) == 0


def test_switch_on_starts_due_rebuild() -> None:
    """Turning the switch back on starts the due rebuild at the next tick."""
    state, _ = run_events(
        state=pinned(),
        events=[SettingsChanged(auto_rebuild=False), OLD],
    )
    state, effects = run_events(
        state=state,
        events=[
            SettingsChanged(auto_rebuild=True),
            Tick(now=START + timedelta(minutes=1)),
        ],
    )
    assert rebuilds(effects=effects) == 1
    assert state.rebuild.running


def test_no_second_rebuild_while_running() -> None:
    """While a rebuild runs, neither TimetableSeen nor Ticks start another."""
    state, _ = step(pinned(), OLD)
    ticks = [
        Tick(now=START + timedelta(hours=hour))
        for hour in range(1, 11)
    ]
    _, effects = run_events(
        state=state,
        events=[TimetableSeen(version='v', schema='4'), *ticks],
    )
    assert rebuilds(effects=effects) == 0


def test_failed_rebuild_backs_off() -> None:
    """A failure waits an hour; a second failure waits two."""
    state = failed_at(minutes=0)
    _, early = step(state, Tick(now=START + timedelta(minutes=59)))
    state, late = step(state, Tick(now=START + timedelta(minutes=61)))
    assert (rebuilds(effects=early), rebuilds(effects=late)) == (0, 1)
    state, _ = step(state, TimetableRebuilt(ok=False))
    assert state.rebuild.retry_at == state.now + timedelta(hours=2)


def test_backoff_caps_at_a_day() -> None:
    """After eight failures in a row the wait is a day, no more."""
    state = failed_at(minutes=0)
    for _ in range(7):
        assert state.rebuild.retry_at is not None
        state, _ = step(state, Tick(now=state.rebuild.retry_at))
        state, _ = step(state, TimetableRebuilt(ok=False))
    assert state.rebuild.failures == 8
    assert state.rebuild.retry_at == state.now + BACKOFF_MAX


def test_rebuilt_ok_waits_before_any_retry() -> None:
    """A success on a file still on schema 4 waits an hour to try again."""
    state, _ = run_events(
        state=pinned(), events=[OLD, TimetableRebuilt(ok=True)],
    )
    _, effects = run_events(
        state=state,
        events=[
            OLD,
            Tick(now=START + timedelta(minutes=59)),
        ],
    )
    assert rebuilds(effects=effects) == 0
    assert state.rebuild.failures == 0


def test_current_schema_clears_outdated() -> None:
    """A later schema-7 file is no longer outdated."""
    state, _ = run_events(
        state=pinned(),
        events=[
            OLD,
            TimetableRebuilt(ok=True),
            TimetableSeen(version='w', schema='7'),
        ],
    )
    assert not state.rebuild.outdated


def test_chooser_works_while_outdated() -> None:
    """With the timetable outdated, picking Train loads its column."""
    walk: list[Event] = [
        OPEN, ColumnLoaded(path=(), column=KINDS), DOWN, NEXT,
    ]
    current = step(pinned(), TimetableSeen(version='v', schema='7'))[0]
    outdated = step(pinned(), OLD)[0]
    assert outdated.rebuild == Rebuild(
        outdated=True,
        running=True,
        failures=0,
        retry_at=None,
        schema='4',
    )
    _, expected = run_events(state=current, events=walk)
    _, effects = run_events(state=outdated, events=walk)
    loads = [effect for effect in effects if isinstance(effect, LoadColumn)]
    assert loads
    assert loads == [
        effect for effect in expected if isinstance(effect, LoadColumn)
    ]


def test_rebuild_now_ignores_version_and_backoff() -> None:
    """Up to date, waiting and switched off, Rebuild now still starts."""
    state, _ = run_events(
        state=pinned(),
        events=[SettingsChanged(auto_rebuild=False), CURRENT],
    )
    later = START + timedelta(hours=1)
    state = replace(
        state, rebuild=replace(state.rebuild, failures=1, retry_at=later),
    )
    state, effects = forced(state=state)
    assert effects == (RebuildTimetable(force=True),)
    assert state.rebuild.running


def test_rebuild_now_while_running_does_nothing() -> None:
    """A rebuild already running is not started again."""
    running, _ = step(pinned(), OLD)
    assert forced(state=running) == (running, ())


def test_missing_timetable_is_due() -> None:
    """With no timetable, online and a key set, a rebuild starts."""
    state, effects = run_events(
        state=pinned(), events=[ApiKeyChanged(present=True), MISSING],
    )
    assert RebuildTimetable(force=False) in effects
    assert state.rebuild.missing and state.rebuild.running


def test_missing_timetable_waits_for_key() -> None:
    """With no key the rebuild waits; setting one starts it."""
    state, effects = run_events(
        state=pinned(),
        events=[
            ApiKeyChanged(present=False),
            MISSING,
            Tick(now=START + timedelta(minutes=1)),
        ],
    )
    assert rebuilds(effects=effects) == 0
    _, effects = run_events(
        state=state,
        events=[
            ApiKeyChanged(present=True),
            Tick(now=START + timedelta(minutes=2)),
        ],
    )
    assert rebuilds(effects=effects) == 1


def test_missing_timetable_waits_for_wifi() -> None:
    """Facts saying Wi-Fi is managed but joined to nothing hold it."""
    offline = replace(device_facts(), wifi=WifiStatus(managed=True))
    _, effects = run_events(
        state=pinned(),
        events=[DeviceProbed(facts=offline), MISSING],
    )
    assert rebuilds(effects=effects) == 0


def building() -> State:
    """
    A state with no timetable and the switch off.

    Returns
    -------
    State
        The state.
    """
    state, _ = run_events(
        state=pinned(),
        events=[SettingsChanged(auto_rebuild=False), MISSING],
    )
    return state


def test_building_screen_details() -> None:
    """The line under the title gives the first reason that applies."""
    switched_off = building()
    idle = replace(
        switched_off,
        settings=replace(switched_off.settings, auto_rebuild=True),
    )
    running = replace(idle, rebuild=replace(idle.rebuild, running=True))
    no_key = replace(idle, panel=replace(idle.panel, key_set=False))
    facts = replace(device_facts(), wifi=WifiStatus(managed=True))
    offline = replace(no_key, panel=replace(no_key.panel, facts=facts))
    failed = replace(
        idle,
        rebuild=replace(
            idle.rebuild,
            failures=1,
            retry_at=START + timedelta(hours=1, minutes=5),
        ),
    )
    details = [
        building_view(state=state).detail
        for state in (running, offline, no_key, failed, idle)
    ]
    assert details == [
        'Hold both knobs for Settings',
        'Waiting for Wi-Fi \u00b7 hold both knobs, then Wi-Fi',
        'Waiting for the API key \u00b7 set it on your phone',
        'Last try failed \u00b7 trying again at 9:05',
        'Hold both knobs for Settings',
    ]
    assert building_view(state=idle).title == 'Building the timetable'


def test_missing_timetable_shows_building_screen() -> None:
    """With no timetable the Building screen is drawn."""
    _, effects = step(pinned(), MISSING)
    views = [effect.view for effect in effects if isinstance(effect, Render)]
    assert isinstance(views[-1], NoticeView)
    assert views[-1].title == 'Building the timetable'


def test_menu_works_on_building_screen() -> None:
    """Knobs do nothing on the Building screen, but the menu opens."""
    state = building()
    for event in (
        KnobPressed(knob=Knob.TOP),
        KnobTurned(knob=Knob.BOTTOM, steps=1),
    ):
        assert step_untouched(state=state, event=event) == (state, ())
    state, effects = step(state, KnobsHeld())
    assert isinstance(effects[-1], Render)
    assert isinstance(effects[-1].view, MenuView)
    state, effects = step(state, KnobPressed(knob=Knob.TOP))
    assert isinstance(effects[-1], Render)
    assert isinstance(effects[-1].view, NoticeView)


def test_rebuild_row_starts_and_shows_device_screen() -> None:
    """The row forces a rebuild and the Device screen says it runs."""
    state = building()
    cursor = MENU_ROWS.index(MenuRow.REBUILD)
    state = replace(
        state, panel=replace(state.panel, menu=Menu(cursor=cursor)),
    )
    state, effects = step(state, KnobPressed(knob=Knob.BOTTOM))
    assert RebuildTimetable(force=True) in effects
    assert state.panel.menu == Menu(
        screen=PanelScreen.DEVICE, cursor=cursor,
    )
    rows = {row.label: row.value for row in device_rows(state=state)}
    assert rows['Timetable'] == 'rebuilding'
    state, _ = step(state, TimetableRebuilt(ok=False))
    rows = {row.label: row.value for row in device_rows(state=state)}
    assert rows['Timetable'] == 'last build failed'


def test_feeds_held_until_there_is_a_timetable() -> None:
    """No feed is fetched or counted without a timetable; one arriving polls."""
    state, effects = run_events(
        state=step(replace(pinned(), view=LiveView.MAP), MISSING)[0],
        events=[
            Tick(now=START + timedelta(seconds=seconds))
            for seconds in range(30, 300, 30)
        ],
    )
    assert not [e for e in effects if isinstance(e, FetchFeed)]
    assert state.quota.calls == 0
    state, effects = run_events(
        state=state,
        events=[CURRENT, Tick(now=state.now + timedelta(seconds=1))],
    )
    assert [e for e in effects if isinstance(e, FetchFeed)]
    views = [e.view for e in effects if isinstance(e, Render)]
    assert not any(isinstance(view, NoticeView) for view in views)
    assert not isinstance(state.last_view, NoticeView)


def test_building_screen_says_rebuild_is_off() -> None:
    """With the switch off it says how to start a rebuild by hand."""
    assert building_view(state=building()).detail == (
        'Rebuild is off \u00b7 hold both knobs, then Rebuild timetable now'
    )


def test_device_row_keeps_the_build_after_a_failure() -> None:
    """A failed rebuild still shows the timetable built before."""
    state, _ = run_events(
        state=pinned(),
        events=[
            TimetableSeen(version='2026-10-16T03:30:00+11:00', schema='7'),
            TimetableRebuilt(ok=False),
        ],
    )
    state, _ = step(forced(state=state)[0], TimetableRebuilt(ok=False))
    rows = {row.label: row.value for row in device_rows(state=state)}
    assert rows['Timetable'].startswith('last build failed \u00b7 16 Oct')
    assert rows['Timetable'].endswith('version 7')


def test_forced_failure_keeps_the_backoff() -> None:
    """A manual rebuild's failure leaves failures and the wait alone."""
    state = failed_at(minutes=0)
    before = state.rebuild
    state, _ = step(forced(state=state)[0], TimetableRebuilt(ok=False))
    assert (state.rebuild.failures, state.rebuild.retry_at) == (
        before.failures,
        before.retry_at,
    )
    assert not state.rebuild.running
