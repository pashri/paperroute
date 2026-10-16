"""Tests for the observers."""

import logging

import pytest

from paperroute.observers import (
    NullObserver,
    ObserverGroup,
    Timings,
    max_rss_mb,
)


class _Failing:
    """An observer whose every call raises."""

    def frame_drawn(self, *, render_ms: float) -> None:
        """Raise."""
        raise RuntimeError(render_ms)

    def feed_parsed(
        self, *, feed: str, fetch_ms: float, parse_ms: float, trips: int
    ) -> None:
        """Raise."""
        raise RuntimeError(feed)

    def ticked(self) -> None:
        """Raise."""
        raise RuntimeError('tick')


def test_timings_records_frames_and_max() -> None:
    """Two frames give a count, the last time and the maximum."""
    timings = Timings()
    timings.frame_drawn(render_ms=5.0)
    timings.frame_drawn(render_ms=3.0)
    snap = timings.snapshot()
    assert snap['frames'] == 2
    assert snap['last_render_ms'] == 3.0
    assert snap['max_render_ms'] == 5.0
    assert isinstance(snap['frame_age_s'], float)
    assert snap['tick_age_s'] is None


def test_timings_records_feed() -> None:
    """A parsed feed appears in the snapshot."""
    timings = Timings()
    timings.feed_parsed(feed='v1/x', fetch_ms=1.0, parse_ms=2.0, trips=7)
    feeds = timings.snapshot()['feeds']
    assert isinstance(feeds, dict)
    assert feeds['v1/x'] == {'fetch_ms': 1.0, 'parse_ms': 2.0, 'trips': 7}


def test_timings_records_tick() -> None:
    """A tick sets the tick age."""
    timings = Timings()
    timings.ticked()
    assert isinstance(timings.snapshot()['tick_age_s'], float)


def test_null_observer_does_nothing() -> None:
    """All three calls return None."""
    null = NullObserver()
    results = [
        null.frame_drawn(render_ms=1.0),  # type: ignore[func-returns-value]
        null.feed_parsed(  # type: ignore[func-returns-value]
            feed='f',
            fetch_ms=1.0,
            parse_ms=1.0,
            trips=1,
        ),
        null.ticked(),  # type: ignore[func-returns-value]
    ]
    assert results == [None, None, None]


def test_observer_group_isolates_a_failing_observer(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A raising observer is logged and the others still run."""
    timings = Timings()
    group = ObserverGroup(observers=[_Failing(), timings])
    with caplog.at_level(logging.ERROR):
        group.frame_drawn(render_ms=2.0)
        group.feed_parsed(feed='f', fetch_ms=1.0, parse_ms=1.0, trips=1)
        group.ticked()
    snap = timings.snapshot()
    assert snap['frames'] == 1
    assert 'f' in snap['feeds']  # type: ignore[operator]
    assert snap['tick_age_s'] is not None
    assert len(caplog.records) == 3


def test_max_rss_mb_is_positive() -> None:
    """Peak memory is a positive number of megabytes."""
    assert 1.0 < max_rss_mb() < 100_000.0
