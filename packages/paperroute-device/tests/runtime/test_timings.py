"""Tests for the kit's timings and observer group."""

import logging
from dataclasses import replace
from typing import Final

import pytest

from paperroute_device.core.device_model import DeviceStatus
from paperroute_device.core.device_settings import DeviceSettings
from paperroute_device.runtime.timings import (
    KitTimings,
    NullObserver,
    ObserverGroup,
)
from paperroute_hardware.ports import PanelRefresh

APP_STATUS: Final[dict[str, object]] = {
    'timetable': '2026-11-10',
    'schema': 9,
    'rebuilding': False,
    'rebuild_failed': False,
    'calls_today': 12,
}
STATUS: Final[DeviceStatus] = DeviceStatus(missing_keys=(), facts=None)


def test_snapshot_keeps_todays_device_key_order() -> None:
    """The app's readings come first in the device object, then the kit's."""
    timings = KitTimings()
    timings.app_seen(status=APP_STATUS)
    timings.status_seen(status=STATUS)
    timings.device_settings_seen(settings=DeviceSettings())
    snapshot = timings.snapshot()
    assert list(snapshot) == [
        'frames',
        'last_render_ms',
        'max_render_ms',
        'frame_age_s',
        'tick_age_s',
        'panel',
        'settings',
        'device',
    ]
    assert snapshot['device'] == {
        **APP_STATUS,
        'key_set': True,
        'asked': None,
        'setup_phase': None,
    }
    device = snapshot['device']
    assert isinstance(device, dict)
    assert list(device) == [
        'timetable',
        'schema',
        'rebuilding',
        'rebuild_failed',
        'calls_today',
        'key_set',
        'asked',
        'setup_phase',
    ]


def test_snapshot_before_reports_has_no_settings_or_device() -> None:
    """Before anything is reported the snapshot holds only the timings."""
    snapshot = KitTimings().snapshot()
    assert 'settings' not in snapshot
    assert 'device' not in snapshot
    assert snapshot['frame_age_s'] is None


def test_snapshot_writes_the_device_settings_as_the_form_does() -> None:
    """The settings are the strings the Settings page's controls hold."""
    timings = KitTimings()
    timings.device_settings_seen(settings=DeviceSettings(rotate=True))
    settings = timings.snapshot()['settings']
    assert isinstance(settings, dict)
    assert settings['rotate'] == 'on'
    assert settings['swap_knobs'] == 'off'


def test_knobs_swapped_reads_device_settings() -> None:
    """The knobs swap once the device settings say so, never before."""
    timings = KitTimings()
    assert timings.knobs_swapped() is False
    timings.device_settings_seen(settings=DeviceSettings(swap_knobs=True))
    assert timings.knobs_swapped() is True
    timings.device_settings_seen(settings=DeviceSettings())
    assert timings.knobs_swapped() is False


def test_work_done_is_ignored(caplog: pytest.LogCaptureFixture) -> None:
    """Background work changes nothing the timings keep and logs nothing."""
    timings = KitTimings()
    before = replace(timings)
    with caplog.at_level(logging.DEBUG):
        timings.work_done(name='sync', ms=5.0)
        NullObserver().work_done(name='sync', ms=5.0)
    assert timings == before
    assert not caplog.records


def test_group_passes_on_past_a_failing_observer(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """One observer raising is logged and the next still hears."""

    class Failing(NullObserver):
        """An observer whose frames raise."""

        def frame_drawn(self, *, render_ms: float) -> None:
            """
            Raise.

            Parameters
            ----------
            render_ms : float
                The render time.

            Raises
            ------
            RuntimeError
                Always.
            """
            raise RuntimeError(render_ms)

    timings = KitTimings()
    group = ObserverGroup(observers=[Failing(), timings])
    with caplog.at_level(logging.ERROR):
        group.frame_drawn(render_ms=2.0)
        group.device_settings_seen(settings=DeviceSettings(rotate=True))
        group.status_seen(status=STATUS)
        group.app_seen(status={'a': 1})
        group.ticked()
        group.work_done(name='sync', ms=1.0)
        group.panel_refreshed(
            refresh=PanelRefresh(full=True, seconds=3.0, ok=True),
        )
    assert timings.frames == 1
    assert timings.settings == DeviceSettings(rotate=True)
    assert timings.device == STATUS
    assert timings.app == {'a': 1}
    assert timings.last_tick_monotonic is not None
    assert timings.panel is not None
    assert timings.panel.full_s == 3.0
    assert 'observer failed' in caplog.text


def test_panel_refreshes_are_kept_by_kind() -> None:
    """Full and partial times are kept apart; a failure only marks it."""
    timings = KitTimings()
    timings.panel_refreshed(refresh=PanelRefresh(full=False, seconds=0.5, ok=True))
    timings.panel_refreshed(refresh=PanelRefresh(full=True, seconds=9.0, ok=False))
    assert timings.panel is not None
    assert (timings.panel.partial_s, timings.panel.full_s) == (0.5, None)
    assert timings.panel.ok is False
    null = NullObserver()
    null.panel_refreshed(refresh=PanelRefresh(full=True, seconds=1.0, ok=True))
    null.device_settings_seen(settings=DeviceSettings())
