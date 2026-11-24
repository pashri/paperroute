"""Shared fixtures for the panel display tests."""

from collections.abc import Iterator

import pytest

from paperroute_hardware.adapters.panel_display import PanelDisplay
from paperroute_hardware.ports import PanelRefresh
from tests.adapters.panel_display.helpers import Clock, GatedController, Rig


@pytest.fixture(name='rig')
def _rig() -> Iterator[Rig]:
    """
    Run a panel display, stopping it afterwards.

    Yields
    ------
    Rig
        The display and its fakes.
    """
    controller = GatedController()
    clock = Clock()
    reports: list[PanelRefresh] = []
    display = PanelDisplay(
        opener=lambda: controller,
        report=reports.append,
        clock=clock,
    )
    rig = Rig(
        display=display,
        controller=controller,
        clock=clock,
        reports=reports,
    )
    yield rig
    controller.gate.set()
    rig.display.stop()
