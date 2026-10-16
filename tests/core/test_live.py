"""Tests for applying live data to departures."""

from dataclasses import replace
from datetime import date, datetime, timedelta

from paperroute.core.live import resolve
from paperroute.core.model import SYDNEY
from paperroute.core.realtime import RealtimeSnapshot, StopUpdate, TripRealtime
from tests.core.factories import START, departure

DEP = departure(departs=START + timedelta(minutes=10), trip_id='t')
BLANK = StopUpdate(
    sequence=None,
    stop_id=None,
    arrival_delay=None,
    departure_delay=None,
    arrival_time=None,
    departure_time=None,
)


def snapshot(
    *,
    updates: tuple[StopUpdate, ...] = (),
    cancelled: bool = False,
    age_s: int = 10,
    trip_id: str = 't',
    start_date: date | None = None,
) -> RealtimeSnapshot:
    """
    A snapshot with one trip.

    Parameters
    ----------
    updates : tuple[StopUpdate, ...]
        Stop updates for the trip.
    cancelled : bool
        Whether the trip is cancelled.
    age_s : int
        Seconds between fetch and START.
    trip_id : str
        Trip ID.
    start_date : date | None
        The run's service date, when the feed gives one.

    Returns
    -------
    RealtimeSnapshot
        Snapshot.
    """
    trip = TripRealtime(
        trip_id=trip_id,
        cancelled=cancelled,
        updates=updates,
        start_date=start_date,
    )
    return RealtimeSnapshot(
        fetched_at=START - timedelta(seconds=age_s),
        trips={trip_id: trip},
    )


def update(
    *,
    seq: int,
    dep: int | None = None,
    arr: int | None = None,
    dep_time: int | None = None,
    stop_id: str | None = None,
) -> StopUpdate:
    """
    A stop update.

    Parameters
    ----------
    seq : int
        Stop sequence.
    dep : int | None
        Departure delay seconds.
    arr : int | None
        Arrival delay seconds.
    dep_time : int | None
        Absolute departure time (epoch seconds).
    stop_id : str | None
        Stop ID.

    Returns
    -------
    StopUpdate
        Update.
    """
    return replace(
        BLANK,
        sequence=seq,
        stop_id=stop_id,
        departure_delay=dep,
        arrival_delay=arr,
        departure_time=dep_time,
    )


def test_resolve_applies_delay_at_origin_and_destination() -> None:
    """Delays at the origin and destination move both times."""
    updates = (update(seq=5, dep=120), update(seq=20, arr=240))
    live = resolve(
        departure=DEP, snapshot=snapshot(updates=updates), now=START
    )
    assert live.live
    assert live.departs == DEP.departs + timedelta(seconds=120)
    assert live.arrives == DEP.arrives + timedelta(seconds=240)


def test_resolve_absolute_time_wins() -> None:
    """An absolute departure time beats a delay."""
    target = DEP.departs + timedelta(minutes=3)
    note = update(seq=5, dep=60, dep_time=int(target.timestamp()))
    live = resolve(
        departure=DEP, snapshot=snapshot(updates=(note,)), now=START
    )
    assert live.departs == target


def test_resolve_propagates_earlier_delay() -> None:
    """With no update at our stops, the last earlier stop's delay applies."""
    live = resolve(
        departure=DEP,
        snapshot=snapshot(updates=(update(seq=2, dep=180),)),
        now=START,
    )
    assert live.departs == DEP.departs + timedelta(seconds=180)
    assert live.arrives == DEP.arrives + timedelta(seconds=180)


def test_resolve_missing_trip_is_scheduled() -> None:
    """A trip absent from the feed keeps scheduled times and isn't live."""
    live = resolve(
        departure=DEP,
        snapshot=snapshot(updates=(update(seq=5, dep=60),), trip_id='other'),
        now=START,
    )
    assert (live.departs, live.live) == (DEP.departs, False)


def test_resolve_alias_matches() -> None:
    """A realtime note under an alias ID applies."""
    aliased = replace(
        departure(departs=DEP.departs, trip_id='kept'), aliases=('old',)
    )
    note = update(seq=5, dep=60, stop_id='o')
    live = resolve(
        departure=aliased,
        snapshot=snapshot(updates=(note,), trip_id='old'),
        now=START,
    )
    assert live.live


def test_resolve_cancelled() -> None:
    """Cancelled trips are flagged."""
    live = resolve(
        departure=DEP, snapshot=snapshot(cancelled=True), now=START
    )
    assert live.cancelled


def test_resolve_not_cancelled_by_default() -> None:
    """A live trip that isn't cancelled isn't flagged."""
    live = resolve(
        departure=DEP,
        snapshot=snapshot(updates=(update(seq=5, dep=60),)),
        now=START,
    )
    assert not live.cancelled


def test_resolve_ignores_stale_snapshot() -> None:
    """A snapshot older than two minutes isn't used."""
    live = resolve(
        departure=DEP,
        snapshot=snapshot(updates=(update(seq=5, dep=600),), age_s=200),
        now=START,
    )
    assert (live.departs, live.live) == (DEP.departs, False)


def test_resolve_without_snapshot_is_scheduled() -> None:
    """With no snapshot at all, times are scheduled."""
    live = resolve(departure=DEP, snapshot=None, now=START)
    assert (live.departs, live.live) == (DEP.departs, False)


def test_resolve_propagates_delay_not_absolute_time() -> None:
    """An earlier stop's absolute time isn't treated as a time at our stop."""
    early = int((DEP.departs - timedelta(minutes=20)).timestamp())
    stop = update(seq=2, dep=60, dep_time=early)
    live = resolve(
        departure=DEP, snapshot=snapshot(updates=(stop,)), now=START
    )
    assert live.departs == DEP.departs + timedelta(seconds=60)


def test_resolve_ignores_another_days_run() -> None:
    """A note for yesterday's run of the same trip ID isn't applied."""
    note = (update(seq=5, dep=600),)
    yesterday = DEP.service_day - timedelta(days=1)
    other = resolve(
        departure=DEP,
        snapshot=snapshot(updates=note, start_date=yesterday),
        now=START,
    )
    assert (other.departs, other.live) == (DEP.departs, False)
    today = resolve(
        departure=DEP,
        snapshot=snapshot(updates=note, start_date=DEP.service_day),
        now=START,
    )
    assert today.live


def test_resolve_alias_matches_by_stop_id() -> None:
    """Under an alias the other copy's stop sequence is ignored."""
    aliased = replace(
        departure(departs=DEP.departs, trip_id='kept'), aliases=('old',)
    )
    other_copy = update(seq=2, dep=90, stop_id='o')
    wrong_seq = update(seq=5, dep=999, stop_id='elsewhere')
    live = resolve(
        departure=aliased,
        snapshot=snapshot(updates=(other_copy, wrong_seq), trip_id='old'),
        now=START,
    )
    assert live.departs == DEP.departs + timedelta(seconds=90)


def test_resolve_carries_arrival_delay_when_no_departure_delay() -> None:
    """An earlier stop with only an arrival delay still carries forward."""
    updates = (update(seq=2, arr=120), update(seq=20))
    live = resolve(
        departure=DEP, snapshot=snapshot(updates=updates), now=START
    )
    assert live.live
    assert live.departs == DEP.departs + timedelta(seconds=120)
    assert live.arrives == DEP.arrives + timedelta(seconds=120)


def test_resolve_update_without_timing_is_not_live() -> None:
    """A note at our stop with no delay or time leaves the departure alone."""
    live = resolve(
        departure=DEP,
        snapshot=snapshot(updates=(update(seq=5), update(seq=20))),
        now=START,
    )
    assert (live.departs, live.live) == (DEP.departs, False)


def test_resolve_delay_adds_real_seconds_over_fall_back() -> None:
    """On the night clocks go back, 5 minutes of delay is 5 real minutes."""
    before = datetime(2026, 4, 5, 2, 58, tzinfo=SYDNEY)
    dep = departure(departs=before, ride=10, trip_id='t')
    now = before - timedelta(minutes=30)
    live = resolve(
        departure=dep,
        snapshot=RealtimeSnapshot(
            fetched_at=now,
            trips={
                't': TripRealtime(
                    trip_id='t',
                    cancelled=False,
                    updates=(update(seq=5, dep=300, arr=300),),
                    start_date=None,
                )
            },
        ),
        now=now,
    )
    assert live.departs.timestamp() - before.timestamp() == 300
    assert live.arrives.timestamp() - dep.arrives.timestamp() == 300
    assert live.departs.tzinfo is SYDNEY
    assert (live.departs.hour, live.departs.minute) == (2, 3)


def test_resolve_snapshot_age_counts_real_seconds() -> None:
    """A snapshot fetched before the clocks go back is older than it reads."""
    fetched = datetime(2026, 4, 5, 2, 59, tzinfo=SYDNEY)
    now = datetime(2026, 4, 5, 2, 3, fold=1, tzinfo=SYDNEY)
    dep = departure(departs=now + timedelta(minutes=5), trip_id='t')
    trip = TripRealtime(
        trip_id='t',
        cancelled=False,
        updates=(update(seq=5, dep=60),),
        start_date=None,
    )
    live = resolve(
        departure=dep,
        snapshot=RealtimeSnapshot(fetched_at=fetched, trips={'t': trip}),
        now=now,
    )
    assert not live.live
