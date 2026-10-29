"""Tests for the periodic run summary."""

import logging
from typing import Final

import pytest

from paperroute.adapters.tfnsw import (
    FeedError,
    FeedThrottled,
    FeedTimedOut,
    FeedUnavailable,
)
from paperroute.summary import (
    SUMMARY_EVERY_S,
    FailReason,
    Summary,
    failure_reason,
    spread,
)

BUSES: Final[str] = 'v1/gtfs/vehiclepos/buses'
TRAINS: Final[str] = 'v2/gtfs/vehiclepos/sydneytrains'


class Clock:
    """A clock the test moves by hand."""

    def __init__(self) -> None:
        self.now = 500.0

    def __call__(self) -> float:
        """
        Read the time.

        Returns
        -------
        float
            Seconds.
        """
        return self.now


def summary_on(clock: Clock) -> Summary:
    """
    Make a summary on a hand-moved clock.

    Parameters
    ----------
    clock : Clock
        The clock.

    Returns
    -------
    Summary
        The summary, its window starting now.
    """
    return Summary(clock=clock)


def test_summary_every_fifteen_minutes() -> None:
    """Summaries come every 15 minutes."""
    assert SUMMARY_EVERY_S == 900


def test_spread_of_nothing_is_none() -> None:
    """No samples give no median or p90."""
    assert spread(values=[]) == {'median': None, 'p90': None}


def test_spread_of_one_sample_is_that_sample() -> None:
    """One sample is both the median and the p90."""
    assert spread(values=[12.34]) == {'median': 12.3, 'p90': 12.3}


def test_spread_gives_median_and_p90() -> None:
    """Ten samples give their median and 90th percentile."""
    values = [float(n) for n in range(1, 11)]
    assert spread(values=values) == {'median': 5.5, 'p90': 9.1}


@pytest.mark.parametrize(
    ('error', 'reason'),
    [
        (FeedThrottled(feed=BUSES, retry_after_s=None), FailReason.THROTTLED),
        (FeedTimedOut(f'{BUSES}: TimeoutError'), FailReason.TIMEOUT),
        (FeedUnavailable(f'{BUSES}: HTTP 502'), FailReason.SERVER),
        (FeedError(f'{BUSES}: HTTP 403'), FailReason.OTHER),
    ],
)
def test_failure_reason_sorts_errors(
    error: FeedError, reason: FailReason,
) -> None:
    """Each failure is put under 429, timeout, 5xx or other."""
    assert failure_reason(error=error) == reason


def test_failure_reason_values() -> None:
    """The reasons read as they appear in the log line."""
    assert [reason.value for reason in FailReason] == [
        '429', 'timeout', '5xx', 'other',
    ]


def test_report_counts_requests_by_feed() -> None:
    """Each feed shows requests sent, OK, failures, backoffs and joins."""
    summary = summary_on(Clock())
    summary.sent(feed=BUSES, fetch_ms=100.0)
    summary.sent(feed=BUSES, fetch_ms=300.0)
    summary.failed(feed=BUSES, reason=FailReason.THROTTLED)
    summary.failed(feed=BUSES, reason=FailReason.TIMEOUT)
    summary.backed_off(feed=BUSES)
    summary.joined(feed=TRAINS)
    feeds = summary.report()['feeds']
    assert feeds == {
        BUSES: {
            'sent': 4,
            'ok': 2,
            'failed': {'429': 1, 'timeout': 1, '5xx': 0, 'other': 0},
            'backed_off': 1,
            'joined': 0,
            'fetch_ms': {'median': 200.0, 'p90': 280.0},
        },
        TRAINS: {
            'sent': 0,
            'ok': 0,
            'failed': {'429': 0, 'timeout': 0, '5xx': 0, 'other': 0},
            'backed_off': 0,
            'joined': 1,
            'fetch_ms': {'median': None, 'p90': None},
        },
    }


def test_report_counts_frames() -> None:
    """Frames drawn show their count and render times."""
    summary = summary_on(Clock())
    for render_ms in (10.0, 20.0, 30.0):
        summary.frame_drawn(render_ms=render_ms)
    report = summary.report()
    assert report['frames'] == 3
    assert report['render_ms'] == {'median': 20.0, 'p90': 28.0}


def test_report_gives_the_window_length() -> None:
    """The report says how many seconds it covers."""
    clock = Clock()
    summary = summary_on(clock)
    clock.now += 61.26
    assert summary.report()['window_s'] == 61.3


def test_tick_before_the_window_ends_logs_nothing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Nothing is logged before 15 minutes have passed."""
    clock = Clock()
    summary = summary_on(clock)
    summary.frame_drawn(render_ms=5.0)
    clock.now += SUMMARY_EVERY_S - 1
    with caplog.at_level(logging.INFO):
        summary.ticked()
    assert not caplog.records


def test_tick_after_the_window_logs_one_info_line(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """After 15 minutes a tick logs one INFO summary carrying the counts."""
    clock = Clock()
    summary = summary_on(clock)
    summary.frame_drawn(render_ms=5.0)
    summary.sent(feed=BUSES, fetch_ms=50.0)
    clock.now += SUMMARY_EVERY_S
    with caplog.at_level(logging.INFO):
        summary.ticked()
    [record] = caplog.records
    assert (record.levelno, record.message) == (logging.INFO, 'summary')
    assert record.__dict__['frames'] == 1
    assert record.__dict__['feeds'][BUSES]['ok'] == 1
    assert record.__dict__['window_s'] == SUMMARY_EVERY_S


def test_tick_after_the_window_starts_a_new_one(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The counts start again after a summary is logged."""
    clock = Clock()
    summary = summary_on(clock)
    summary.frame_drawn(render_ms=5.0)
    clock.now += SUMMARY_EVERY_S
    summary.ticked()
    clock.now += 10
    with caplog.at_level(logging.INFO):
        summary.ticked()
    assert not caplog.records
    report = summary.report()
    assert (report['frames'], report['feeds'], report['window_s']) == (
        0, {}, 10.0,
    )

