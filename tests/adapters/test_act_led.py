"""Tests for the green LED observer."""

import logging
from pathlib import Path

import pytest

from paperroute.adapters.act_led import ActLed


def test_led_goes_steady_on_first_frame(tmp_path: Path) -> None:
    """The first frame sets the trigger to default-on."""
    (tmp_path / 'trigger').write_text('mmc0\n')
    led = ActLed(root=tmp_path)
    led.ticked()
    assert (tmp_path / 'trigger').read_text() == 'mmc0\n'
    led.frame_drawn(render_ms=1.0)
    assert (tmp_path / 'trigger').read_text() == 'default-on'


def test_led_missing_sysfs_warns_once(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A missing LED directory logs one warning across two frames."""
    led = ActLed(root=tmp_path / 'missing')
    with caplog.at_level(logging.WARNING):
        led.frame_drawn(render_ms=1.0)
        led.frame_drawn(render_ms=1.0)
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
