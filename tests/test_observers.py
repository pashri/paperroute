"""Tests for the observers."""

import logging
import time
from dataclasses import replace

import pytest

from paperroute.adapters.settings_form import form_values
from paperroute.core.device_model import DeviceStatus, WifiPhase, WifiSetup
from paperroute.core.model import Pin
from paperroute.core.saved import Settings
from paperroute.observers import (
    NullObserver,
    ObserverGroup,
    PanelTimes,
    Timings,
)
from paperroute.ports import PanelRefresh
from tests.core.factories import pin_of
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

PINS: tuple[Pin, ...] = (pin_of('215020', '200060'),)


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

    def pins_seen(self, *, pins: tuple[Pin, ...], shown: int | None) -> None:
        """Raise."""
        raise RuntimeError(pins, shown)

    def panel_refreshed(self, *, refresh: PanelRefresh) -> None:
        """Raise."""
        raise RuntimeError(refresh)


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
    assert timings.snapshot()['settings'] == form_values(
        settings=Settings(auto_rebuild=False),
    )


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
    assert snap['settings'] == form_values(
        settings=Settings(auto_rebuild=False),
    )
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


def test_timings_count_pin_revisions() -> None:
    """Each report of the pins raises the revision by one."""
    timings = Timings()
    assert timings.pins is None
    timings.pins_seen(pins=(), shown=None)
    assert timings.pins is not None
    assert timings.pins.revision == 1
    timings.pins_seen(pins=PINS, shown=0)
    assert timings.pins.revision == 2
    assert timings.pins.pins == PINS
    assert timings.pins.shown == 0


def test_group_passes_pins_on() -> None:
    """A group forwards the pins past an observer that fails."""
    timings = Timings()
    group = ObserverGroup(observers=[_Failing(), timings])
    group.pins_seen(pins=PINS, shown=None)
    NullObserver().pins_seen(pins=PINS, shown=None)
    assert timings.pins is not None
    assert timings.pins.pins == PINS


def test_timings_without_a_panel_report_none() -> None:
    """With no panel the snapshot's panel entry is None."""
    assert Timings().snapshot()['panel'] is None


def test_timings_keep_panel_times() -> None:
    """Good refreshes set their kind's time; a failure only clears ok."""
    timings = Timings(panel=PanelTimes())
    assert timings.snapshot()['panel'] == {
        'partial_s': None,
        'full_s': None,
        'ok': True,
    }
    timings.panel_refreshed(
        refresh=PanelRefresh(full=False, seconds=0.5, ok=True),
    )
    timings.panel_refreshed(
        refresh=PanelRefresh(full=True, seconds=4.8, ok=False),
    )
    assert timings.snapshot()['panel'] == {
        'partial_s': 0.5,
        'full_s': None,
        'ok': False,
    }
    timings.panel_refreshed(
        refresh=PanelRefresh(full=True, seconds=4.8, ok=True),
    )
    assert timings.panel == PanelTimes(partial_s=0.5, full_s=4.8, ok=True)


def test_timings_start_panel_times_on_a_refresh() -> None:
    """A refresh before the panel is marked open starts its times."""
    timings = Timings()
    timings.panel_refreshed(
        refresh=PanelRefresh(full=True, seconds=4.0, ok=True),
    )
    assert timings.panel == PanelTimes(full_s=4.0)


def test_group_passes_panel_refreshes_on() -> None:
    """The group passes refreshes on past a failing observer."""
    timings = Timings()
    refresh = PanelRefresh(full=False, seconds=0.5, ok=True)
    ObserverGroup(observers=[_Failing(), timings]).panel_refreshed(
        refresh=refresh,
    )
    NullObserver().panel_refreshed(refresh=refresh)
    assert timings.panel == PanelTimes(partial_s=0.5)


def test_timings_knobs_swapped_follows_the_settings() -> None:
    """The knobs are not swapped until the settings in force say so."""
    timings = Timings()
    assert not timings.knobs_swapped()
    timings.settings_seen(settings=Settings(swap_knobs=True))
    assert timings.knobs_swapped()
    timings.settings_seen(settings=Settings())
    assert not timings.knobs_swapped()
