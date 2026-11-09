"""Tests for the timetable window warning in the board's banner."""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import Final

import pytest

from paperroute_device.core.device_events import ApiKeyChanged
from paperroute_testkit.core.factories import START
from paperroute_transit.core.banners import (
    NO_KEY,
    TIMETABLE_OUT_OF_DATE,
    device_banner,
    timetable_banner,
    timetable_ends,
)
from paperroute_transit.core.messages import TimetableSeen
from paperroute_transit.core.state import step
from paperroute_transit.core.state_model import State
from paperroute_transit.core.state_views import stop_view_of
from tests.core.test_idle_frames import board, idle
from tests.core.test_state import CENTRE, fresh, loaded

FRIDAY: Final[date] = date(2026, 10, 16)  # START's day in Sydney
ENDS_FRIDAY: Final[str] = 'Timetable ends Friday. Check the internet and key.'


def ending(*, state: State, last_day: date | None) -> State:
    """
    Give a state whose timetable covers days up to last_day.

    Parameters
    ----------
    state : State
        State.
    last_day : date | None
        The last day the timetable covers; None for no window.

    Returns
    -------
    State
        State with the window recorded.
    """
    return replace(
        state,
        rebuild=replace(state.rebuild, last_day=last_day),
    )


def at_time(*, state: State, now: datetime) -> State:
    """
    Move a state's clock without stepping it.

    Parameters
    ----------
    state : State
        State.
    now : datetime
        New time.

    Returns
    -------
    State
        State at now, with no cycle start ahead of it.
    """
    return replace(state, now=now, cycle=replace(state.cycle, started=None))


def test_timetable_ends_wording() -> None:
    """The weekday is the last day the timetable covers."""
    assert timetable_ends(last_day=FRIDAY) == ENDS_FRIDAY
    assert TIMETABLE_OUT_OF_DATE == (
        'Timetable out of date. Check the internet and key.'
    )


@pytest.mark.parametrize(
    ('days_after', 'banner'),
    [
        (3, None),
        (2, 'Timetable ends Sunday. Check the internet and key.'),
        (1, 'Timetable ends Saturday. Check the internet and key.'),
        (0, ENDS_FRIDAY),
        (-1, TIMETABLE_OUT_OF_DATE),
        (-30, TIMETABLE_OUT_OF_DATE),
    ],
)
def test_banner_from_three_days_left(
    days_after: int, banner: str | None,
) -> None:
    """Three days or fewer left, today counted, warns; then out of date."""
    last_day = FRIDAY + timedelta(days=days_after)
    state = ending(state=fresh(), last_day=last_day)
    assert timetable_banner(state=state) == banner
    assert device_banner(state=state) == banner


def test_no_window_no_banner() -> None:
    """A timetable that records no window never warns."""
    state = ending(state=fresh(), last_day=None)
    assert timetable_banner(state=state) is None


def test_banner_turns_at_midnight_in_the_city() -> None:
    """The day is the city's: Sydney's midnight, not UTC's, expires it."""
    state = ending(state=fresh(), last_day=FRIDAY)
    # 23:59 and 00:00 AEDT, both on Friday in UTC
    before = datetime(2026, 10, 16, 12, 59, tzinfo=UTC)
    after = datetime(2026, 10, 16, 13, 0, tzinfo=UTC)
    assert timetable_banner(
        state=at_time(state=state, now=before),
    ) == ENDS_FRIDAY
    assert timetable_banner(
        state=at_time(state=state, now=after),
    ) == TIMETABLE_OUT_OF_DATE


def test_banner_appears_at_midnight_three_days_out() -> None:
    """The warning starts at the city's midnight three days before."""
    last_day = date(2026, 10, 19)
    state = ending(state=fresh(), last_day=last_day)
    # 23:59 on Friday and 00:00 on Saturday, in AEDT
    before = datetime(2026, 10, 16, 12, 59, tzinfo=UTC)
    after = datetime(2026, 10, 16, 13, 0, tzinfo=UTC)
    assert timetable_banner(state=at_time(state=state, now=before)) is None
    assert timetable_banner(state=at_time(state=state, now=after)) == (
        'Timetable ends Monday. Check the internet and key.'
    )


def test_banner_changes_at_most_once_a_day() -> None:
    """Over a week the text changes only at the city's midnights."""
    state = ending(state=fresh(), last_day=FRIDAY + timedelta(days=3))
    seen: list[tuple[datetime, str | None]] = []
    now = START
    while now < START + timedelta(days=6):
        banner = timetable_banner(state=at_time(state=state, now=now))
        if not seen or seen[-1][1] != banner:
            seen.append((now, banner))
        now += timedelta(minutes=10)
    zone = state.city.zone
    assert [banner for _, banner in seen] == [
        None,
        'Timetable ends Monday. Check the internet and key.',
        TIMETABLE_OUT_OF_DATE,
    ]
    assert all(
        moment.astimezone(zone).time().hour == 0
        and moment.astimezone(zone).time().minute < 10
        for moment, _ in seen[1:]
    )


def test_wifi_and_key_warnings_come_first() -> None:
    """A missing key is said before the timetable's end."""
    state = ending(state=loaded(state=fresh()), last_day=FRIDAY)
    state, _ = step(state, ApiKeyChanged(present=False))
    assert device_banner(state=state) == NO_KEY


def test_stop_view_shows_the_warning() -> None:
    """The Stop view's banner warns in the device warning style."""
    state = ending(state=loaded(state=fresh()), last_day=FRIDAY)
    view = stop_view_of(state=state)
    assert view.banner == ENDS_FRIDAY
    assert view.warning


def test_timetable_seen_records_the_last_day() -> None:
    """The last day rides on the timetable seen event into the state."""
    state, _ = step(
        fresh(), TimetableSeen(version='v1', last_day=FRIDAY),
    )
    assert state.rebuild.last_day == FRIDAY
    state, _ = step(state, TimetableSeen(version='v2'))
    assert state.rebuild.last_day is None


def test_warning_adds_no_idle_frames() -> None:
    """A board warning of the timetable's end draws no extra frames."""
    plain = idle(state=board())
    warned = idle(
        state=board(start=ending(state=fresh(centre=CENTRE), last_day=FRIDAY)),
    )
    assert stop_view_of(state=board(
        start=ending(state=fresh(centre=CENTRE), last_day=FRIDAY),
    )).banner == ENDS_FRIDAY
    assert not warned.stray
    assert warned.renders == plain.renders
