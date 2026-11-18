"""Tests for the once-per-boot splash."""

import logging
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

import numpy as np
import pytest
from numpy.typing import NDArray

from paperroute_device.boot_splash import splash_once
from paperroute_device_testkit.hardware_fakes import FakePanelBus
from paperroute_hardware.adapters.epaper import Uc8179
from paperroute_hardware.adapters.panel_display import PanelDisplay
from paperroute_hardware.render.panel_bits import grey_planes, oriented

WAIT: Final[float] = 5  # seconds


@dataclass
class Opener:
    """
    Opens the panel on a fake bus, once its gate is open.

    Attributes
    ----------
    bus : FakePanelBus
        The bus the panel is opened on.
    gate : threading.Event
        Set while opening may go ahead.
    entered : threading.Event
        Set when the worker first tries to open.
    """

    bus: FakePanelBus = field(default_factory=FakePanelBus)
    gate: threading.Event = field(default_factory=threading.Event)
    entered: threading.Event = field(default_factory=threading.Event)

    def __post_init__(self) -> None:
        self.gate.set()

    def __call__(self) -> Uc8179:
        """
        Open the panel.

        Returns
        -------
        Uc8179
            A controller on the fake bus.
        """
        self.entered.set()
        assert self.gate.wait(timeout=WAIT)
        return Uc8179(bus=self.bus, clock=lambda: self.bus.now_s)


@dataclass
class Rig:
    """
    A panel display and the opener behind it.

    Attributes
    ----------
    panel : PanelDisplay
        The display under test.
    opener : Opener
        Opens its fake panel.
    """

    panel: PanelDisplay
    opener: Opener


@pytest.fixture(name='rig')
def _rig() -> Iterator[Rig]:
    """
    Make a panel display on a fake bus and stop it afterwards.

    Yields
    ------
    Rig
        The display and its opener.
    """
    opener = Opener()
    panel = PanelDisplay(opener=opener, report=lambda refresh: None)
    yield Rig(panel=panel, opener=opener)
    opener.gate.set()
    panel.stop()


def picture_file(*, path: Path) -> NDArray[np.uint8]:
    """
    Write a splash picture whose top rows are black.

    Parameters
    ----------
    path : Path
        Where to write the raw picture.

    Returns
    -------
    NDArray[np.uint8]
        The picture written.
    """
    frame = np.full((480, 800), 170, dtype=np.uint8)
    frame[:40] = 0
    path.write_bytes(frame.tobytes())
    return frame


def drew(*, bus: FakePanelBus, frame: NDArray[np.uint8]) -> bool:
    """
    Tell whether a full refresh of the frame was sent.

    Parameters
    ----------
    bus : FakePanelBus
        The bus the panel was driven over.
    frame : NDArray[np.uint8]
        The frame, as drawn.

    Returns
    -------
    bool
        True when both grey planes were sent.
    """
    planes = grey_planes(frame=frame)
    sent = {('command', (0x10, planes[0])), ('command', (0x13, planes[1]))}
    return sent <= set(bus.log)


def test_splash_once_draws_the_picture(rig: Rig, tmp_path: Path) -> None:
    """The first call asks for the splash and leaves the marker."""
    picture = picture_file(path=tmp_path / 'on.raw')
    marker = tmp_path / 'marker'
    assert splash_once(
        panel=rig.panel,
        image=tmp_path / 'on.raw',
        marker=marker,
        state=tmp_path / 'state.json',
    )
    assert rig.panel.settle(timeout=WAIT)
    assert drew(bus=rig.opener.bus, frame=picture)
    assert marker.exists()


def test_splash_once_follows_the_rotate_setting(
    rig: Rig, tmp_path: Path,
) -> None:
    """A rotated board draws the splash upside down."""
    picture = picture_file(path=tmp_path / 'on.raw')
    state = tmp_path / 'state.json'
    state.write_text(
        '{"device": {"settings": {"rotate": true}}}', encoding='utf-8',
    )
    assert splash_once(
        panel=rig.panel,
        image=tmp_path / 'on.raw',
        marker=tmp_path / 'marker',
        state=state,
    )
    assert rig.panel.settle(timeout=WAIT)
    turned = oriented(frame=picture, turned=True)
    assert drew(bus=rig.opener.bus, frame=turned)


def test_splash_once_skips_a_restart_in_the_same_boot(
    rig: Rig, tmp_path: Path,
) -> None:
    """With the marker there, nothing is asked for and nothing opens."""
    picture_file(path=tmp_path / 'on.raw')
    marker = tmp_path / 'marker'
    marker.touch()
    assert not splash_once(
        panel=rig.panel,
        image=tmp_path / 'on.raw',
        marker=marker,
        state=tmp_path / 'state.json',
    )
    assert rig.panel.settle(timeout=WAIT)
    assert not rig.opener.entered.is_set()


def test_splash_once_without_a_panel_touches_nothing(tmp_path: Path) -> None:
    """Without a panel no marker is written."""
    assert not splash_once(
        panel=None,
        image=tmp_path / 'on.raw',
        marker=tmp_path / 'marker',
        state=tmp_path / 'state.json',
    )
    assert not (tmp_path / 'marker').exists()


def test_splash_once_skips_an_unwritable_marker(
    rig: Rig, tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """A marker that cannot be written skips the splash with one line."""
    picture_file(path=tmp_path / 'on.raw')
    with caplog.at_level(logging.INFO):
        assert not splash_once(
            panel=rig.panel,
            image=tmp_path / 'on.raw',
            marker=tmp_path / 'gone' / 'marker',
            state=tmp_path / 'state.json',
        )
    assert [
        (record.levelno, record.message.startswith('splash skipped: '))
        for record in caplog.records
    ] == [(logging.INFO, True)]
    assert not rig.opener.entered.is_set()


def test_board_start_does_not_wait_for_the_splash(
    rig: Rig, tmp_path: Path,
) -> None:
    """The board carries on and shows its frame while the splash is stuck."""
    picture_file(path=tmp_path / 'on.raw')
    rig.opener.gate.clear()
    assert splash_once(
        panel=rig.panel,
        image=tmp_path / 'on.raw',
        marker=tmp_path / 'marker',
        state=tmp_path / 'state.json',
    )
    assert rig.opener.entered.wait(timeout=WAIT)
    assert not rig.panel.settle(timeout=0.05)
    frame = np.full((480, 800), 255, dtype=np.uint8)
    rig.panel.show(frame=frame)
    assert not rig.panel.settle(timeout=0.05)
    rig.opener.gate.set()
    assert rig.panel.settle(timeout=WAIT)
    assert drew(bus=rig.opener.bus, frame=frame)
