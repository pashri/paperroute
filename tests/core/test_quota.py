"""Tests for counting API calls per Sydney day."""

from dataclasses import replace
from datetime import date, datetime, timedelta

from paperroute.core.messages import (
    FetchFeed,
    QuitRequested,
    SaveState,
    Stop,
    Tick,
)
from paperroute.core.model import SYDNEY
from paperroute.core.quota import (
    DAILY_LIMIT,
    QUOTA_SAVE_EVERY,
    calls_today,
    counted,
    quota_only,
    with_calls,
)
from paperroute.core.saved import Quota
from paperroute.core.state import step
from paperroute.core.state_model import State
from paperroute.core.state_saves import SAVE_AFTER
from paperroute.core.state_views import saved_of
from tests.core.steps import wakeful
from tests.core.test_state import BUSES, HOLD, at, fresh, loaded, pinned, saves

DAY = date(2026, 10, 21)
NEXT_DAY = date(2026, 10, 22)
TWO_FEEDS = (BUSES, 'v1/gtfs/realtime/ferries')


def fetching(*, state: State) -> State:
    """
    Make the next tick poll the trip feeds.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    State
        State that has never fetched.
    """
    return replace(state, last_fetch=None)


def test_constants() -> None:
    """The limit and the save interval are as agreed."""
    assert DAILY_LIMIT == 60_000
    assert QUOTA_SAVE_EVERY == timedelta(minutes=10)


def test_fetches_are_counted() -> None:
    """A tick fetching two trip feeds and one alerts feed counts three."""
    now = datetime(2026, 10, 21, 8, 0, tzinfo=SYDNEY)
    state = replace(loaded(state=fresh(now=now)), feeds=TWO_FEEDS)
    state, effects = step(state, Tick(now=now))
    assert sum(isinstance(e, FetchFeed) for e in effects) == 3
    assert state.quota == Quota(day=DAY, calls=3)


def test_quota_rolls_over_at_sydney_midnight() -> None:
    """The count restarts on the first call of the next Sydney day."""
    late = datetime(2026, 10, 21, 23, 59, 59, tzinfo=SYDNEY)
    early = datetime(2026, 10, 22, 0, 0, 5, tzinfo=SYDNEY)
    state = wakeful(state=loaded(state=fresh(now=late)))
    state = replace(state, quota=Quota(day=DAY, calls=500))
    state, _ = step(state, Tick(now=late))
    assert state.quota == Quota(day=DAY, calls=502)
    state, _ = step(fetching(state=state), Tick(now=early))
    assert state.quota == Quota(day=NEXT_DAY, calls=1)


def test_quota_rolls_over_on_a_daylight_saving_day() -> None:
    """Midnight after the clocks go forward still starts a new day."""
    late = datetime(2026, 10, 4, 23, 59, tzinfo=SYDNEY)
    early = datetime(2026, 10, 5, 0, 0, tzinfo=SYDNEY)
    state = wakeful(state=loaded(state=fresh(now=late)))
    state = replace(state, quota=Quota(day=date(2026, 10, 4), calls=9))
    state, _ = step(state, Tick(now=late))
    assert state.quota == Quota(day=date(2026, 10, 4), calls=11)
    state, _ = step(fetching(state=state), Tick(now=early))
    assert state.quota == Quota(day=date(2026, 10, 5), calls=1)


def test_calls_today_is_zero_on_a_new_day() -> None:
    """Calls made on another day do not count."""
    quota = Quota(day=DAY, calls=7)
    assert calls_today(quota=quota, day=DAY) == 7
    assert calls_today(quota=quota, day=NEXT_DAY) == 0
    assert calls_today(quota=Quota(), day=DAY) == 0


def test_counted_starts_again_on_a_new_day() -> None:
    """Adding calls on a new day starts from those calls."""
    quota = Quota(day=DAY, calls=7)
    assert counted(quota=quota, day=DAY, calls=2).calls == 9
    assert counted(quota=quota, day=NEXT_DAY, calls=2) == Quota(
        day=NEXT_DAY,
        calls=2,
    )


def test_with_calls_ignores_other_effects() -> None:
    """With no fetch the state is returned as it is."""
    state = loaded(state=fresh())
    assert with_calls(state=state, effects=(Stop(),)) is state


def test_quota_only() -> None:
    """Only a difference in the quota alone counts."""
    saved = saved_of(state=fresh())
    more = replace(saved, quota=Quota(day=DAY, calls=1))
    assert quota_only(before=saved, after=more)
    assert not quota_only(before=saved, after=saved)
    assert not quota_only(before=saved, after=replace(more, current=1))


def test_quota_only_change_saves_every_ten_minutes() -> None:
    """Polling alone writes the quota once in ten minutes."""
    state = loaded(state=fresh())
    for seconds in range(30, 600, 30):
        state, effects = step(state, at(seconds=seconds))
        assert not saves(effects=effects), seconds
    assert state.quota.calls > 0
    state, effects = step(state, at(seconds=600))
    written = saves(effects=effects)
    assert len(written) == 1
    assert written[0].saved.quota.calls > 0
    assert state.saving.saved == written[0].saved
    assert state.saving.quota_at == state.now


def test_quota_change_does_not_delay_pin_save() -> None:
    """A pin toggle is saved 3 s after it, however often feeds are polled."""
    state, _ = step(loaded(state=pinned()), at(seconds=10))
    state, _ = step(state, HOLD)
    for seconds in (11, 12):
        state, effects = step(fetching(state=state), at(seconds=seconds))
        assert not saves(effects=effects)
    state, effects = step(
        fetching(state=state),
        at(seconds=10 + SAVE_AFTER.seconds),
    )
    assert len(saves(effects=effects)) == 1
    assert saves(effects=effects)[0].saved.quota.calls > 0


def test_quit_saves_quota_at_once() -> None:
    """Quitting writes a quota that is not yet ten minutes old."""
    state, _ = step(loaded(state=fresh()), at(seconds=30))
    assert state.quota.calls > 0
    state, effects = step(state, QuitRequested())
    assert effects == (SaveState(saved=saved_of(state=state)), Stop())
    assert saves(effects=effects)[0].saved.quota == state.quota

