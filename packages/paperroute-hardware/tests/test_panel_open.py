"""Tests for choosing the panel times."""

import pytest

from paperroute_hardware.adapters.panel_display import PanelDisplay
from paperroute_hardware.panel_open import panel_times_for
from paperroute_hardware.ports import PanelTimes
from tests.adapters.test_panel_display import GatedController


def test_panel_times_for_not_wanted() -> None:
    """With no --panel there is no line."""
    assert panel_times_for(wanted=False, panel=None) is None


def test_panel_times_for_failed_open() -> None:
    """A wanted panel that did not open shows as not answering."""
    assert panel_times_for(wanted=True, panel=None) == PanelTimes(ok=False)


def test_panel_times_for_opened() -> None:
    """A panel that opened starts with fresh times."""
    panel = PanelDisplay(
        opener=GatedController, report=lambda refresh: None,
    )
    try:
        assert panel_times_for(wanted=True, panel=panel) == PanelTimes()
    finally:
        panel.stop()
