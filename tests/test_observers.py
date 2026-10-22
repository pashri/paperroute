"""Tests for the observers."""

import logging
import time
from dataclasses import replace

import pytest

from paperroute.core.device_model import DeviceStatus, WifiPhase, WifiSetup
from paperroute.core.saved import Settings
from paperroute.observers import NullObserver, ObserverGroup, Timings
from tests.fakes import device_facts

STATUS: DeviceStatus = DeviceStatus(
    timetable='2026-10-21',
    schema='6',
    rebuilding=False,
    rebuild_failed=True,
    calls_today=12,
    key_set=True,
    facts=device_facts(),
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

    def settings_seen(self, *, settings: Settings) -> None:
        """Raise."""
        raise RuntimeError(settings)

    def status_seen(self, *, status: DeviceStatus) -> None:
        """Raise."""
        raise RuntimeError(status)


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
    entry = feeds['v1/x']
    assert (entry['fetch_ms'], entry['parse_ms'], entry['trips']) == (
        1.0,
        2.0,
        7,
    )


def test_timings_records_tick() -> None:
    """A tick sets the tick age."""
    timings = Timings()
    timings.ticked()
    assert isinstance(timings.snapshot()['tick_age_s'], float)


def test_timings_report_settings() -> None:
    """The settings appear in the snapshot once reported."""
    timings = Timings()
    assert 'settings' not in timings.snapshot()
    timings.settings_seen(settings=Settings(auto_rebuild=False))
    assert timings.snapshot()['settings'] == {'auto_rebuild': False}


def test_null_observer_does_nothing() -> None:
    """Every call returns None."""
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
        null.settings_seen(  # type: ignore[func-returns-value]
            settings=Settings(),
        ),
    ]
    assert results == [None, None, None, None]


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
        group.settings_seen(settings=Settings(auto_rebuild=False))
    snap = timings.snapshot()
    assert snap['frames'] == 1
    assert 'f' in snap['feeds']  # type: ignore[operator]
    assert snap['tick_age_s'] is not None
    assert snap['settings'] == {'auto_rebuild': False}
    assert len(caplog.records) == 4


def test_timings_feed_last_ok() -> None:
    """A parsed feed records when it last worked and how long ago."""
    timings = Timings()
    before = time.time()
    timings.feed_parsed(feed='f', fetch_ms=1.0, parse_ms=2.0, trips=3)
    feeds = timings.snapshot()['feeds']
    assert isinstance(feeds, dict)
    entry = feeds['f']
    assert before <= entry['last_ok'] <= time.time()
    assert 0 <= entry['age_s'] < 5


def test_status_json_has_no_facts() -> None:
    """The device status is reported without the device facts."""
    timings = Timings()
    assert 'device' not in timings.snapshot()
    timings.status_seen(status=STATUS)
    snap = timings.snapshot()
    assert snap['device'] == {
        'timetable': '2026-10-21',
        'schema': '6',
        'rebuilding': False,
        'rebuild_failed': True,
        'calls_today': 12,
        'key_set': True,
        'asked': None,
        'setup_phase': None,
    }
    assert 'facts' not in str(snap)
    assert 'secret' not in str(snap)


def test_null_and_group_take_status() -> None:
    """Both pass on or ignore a status without failing."""
    NullObserver().status_seen(status=STATUS)
    timings = Timings()
    ObserverGroup(observers=[_Failing(), timings]).status_seen(status=STATUS)
    assert timings.device == STATUS


def test_status_json_has_no_password() -> None:
    """A setup's phase is reported, never its hotspot password."""
    setup = WifiSetup(phase=WifiPhase.HOTSPOT, password='7Q4K9W2M')
    timings = Timings()
    timings.status_seen(status=replace(STATUS, setup=setup))
    snap = timings.snapshot()
    assert snap['device']['setup_phase'] == 'hotspot'  # type: ignore[index]
    assert '7Q4K' not in str(snap)
