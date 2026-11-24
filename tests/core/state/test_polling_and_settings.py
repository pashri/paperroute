"""Tests for state: polling and settings."""

from dataclasses import replace
from datetime import date, datetime, timedelta

from paperroute_device.core.kit_events import Tick
from paperroute_sydney.city import SYDNEY, SYDNEY_ZONE
from paperroute_testkit.core.factories import START
from paperroute_testkit.core.kit_steps import settings_posted, step
from paperroute_transit.core.alert_state import ALERT_EVERY
from paperroute_transit.core.auto_model import AutoState
from paperroute_transit.core.feed_plan import FeedKind
from paperroute_transit.core.messages import (
    Effect,
    FetchFeed,
    HolidaysLoaded,
    LoadHolidays,
    SettingsChanged,
    TimetableSeen,
)
from paperroute_transit.core.reducers import initial_state
from paperroute_transit.core.saved import Rate, seeded
from paperroute_transit.core.state_model import State
from paperroute_transit.core.state_views import saved_of
from paperroute_transit.core.stop_view import StopView
from tests.core.state.helpers import ALERTS, BUSES, PIN, fresh, loaded, pinned

NOON = datetime(2026, 10, 16, 12, 0, tzinfo=SYDNEY_ZONE)


def fetches_at(*, state: State, now: datetime) -> list[Effect]:
    """
    Give the trip-updates fetches a Tick asks for.

    Parameters
    ----------
    state : State
        State before the Tick.
    now : datetime
        The Tick's time.

    Returns
    -------
    list[Effect]
        The FetchFeed effects.
    """
    _, effects = step(state, Tick(now=now))
    return [effect for effect in effects if isinstance(effect, FetchFeed)]


def test_faster_rate_applies_next_tick() -> None:
    """A faster off-peak rate fetches on the next Tick past it."""
    state, _ = step(loaded(state=fresh(now=NOON)), Tick(now=NOON))
    later = NOON + timedelta(seconds=40)
    assert not fetches_at(state=state, now=later)
    state, _ = step(state, SettingsChanged(offpeak_rate=Rate.FAST))
    assert fetches_at(state=state, now=later) == [FetchFeed(feed=BUSES)]


def test_off_peak_waits_two_minutes() -> None:
    """Off-peak, a weekday polls every two minutes."""
    eleven = NOON - timedelta(hours=1)
    state, _ = step(loaded(state=fresh(now=eleven)), Tick(now=eleven))
    assert not fetches_at(state=state, now=eleven + timedelta(seconds=60))
    assert fetches_at(state=state, now=eleven + timedelta(seconds=120))


def test_timetable_seen_loads_holidays() -> None:
    """A new timetable asks for its holidays; another version's are stale."""
    state, effects = step(pinned(), TimetableSeen(version='v'))
    assert LoadHolidays(timetable='v') in effects
    holidays = frozenset({date(2026, 1, 26)})
    other = HolidaysLoaded(dates=holidays, timetable='w')
    assert step(state, other) == (state, ())
    state, _ = step(state, HolidaysLoaded(dates=holidays, timetable='v'))
    assert state.calendar.holidays == holidays


def test_settings_reach_stop_view() -> None:
    """Larger text and inverted lists show in the redrawn Stop view."""
    state, _ = step(pinned(), settings_posted(larger_text=True))
    state, _ = step(state, Tick(now=START + timedelta(seconds=5)))
    assert isinstance(state.last_view, StopView)
    assert state.last_view.large
    assert not state.last_view.inverted
    state, _ = step(state, settings_posted(inverted_lists=True))
    state, _ = step(state, Tick(now=START + timedelta(seconds=10)))
    assert isinstance(state.last_view, StopView)
    assert state.last_view.inverted


def test_alerts_counted_in_quota() -> None:
    """The alert fetches of a tick count against the day's quota."""
    state = loaded(state=fresh())
    after, effects = step(state, Tick(now=START + ALERT_EVERY))
    fetches = [e for e in effects if isinstance(e, FetchFeed)]
    assert FetchFeed(feed=ALERTS, kind=FeedKind.ALERTS) in fetches
    assert after.quota.calls - state.quota.calls == len(fetches)


def test_auto_saved_and_restored() -> None:
    """The auto pin state is saved and comes back at start-up."""
    auto = AutoState(overridden_at=START)
    state = fresh()
    assert saved_of(state=replace(state, auto=auto)).auto == auto
    saved = replace(seeded(pin=PIN, city=SYDNEY), auto=auto)
    assert initial_state(
        saved=saved,
        now=START,
        city=SYDNEY,
    ).auto == auto
