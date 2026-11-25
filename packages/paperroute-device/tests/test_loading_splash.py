"""Tests for the picture shown while the board switches city."""

import logging
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pytest
from numpy.typing import NDArray

from paperroute_device.loading_splash import (
    RELEASE_SCREENS,
    LoadingHold,
    loading_image,
)

FRAME = np.full((480, 800), 85, dtype=np.uint8)
BOARD = np.zeros((480, 800), dtype=np.uint8)


@dataclass
class Shown:
    """
    A display that records each frame shown.

    Attributes
    ----------
    frames : list[tuple[NDArray[np.uint8], bool, bool]]
        Each frame with its full and turned flags.
    """

    frames: list[tuple[NDArray[np.uint8], bool, bool]] = field(
        default_factory=list,
    )

    def show(
        self,
        *,
        frame: NDArray[np.uint8],
        full: bool = False,
        turned: bool = False,
        quick: bool = False,
        asleep: bool = False,
        clear: bool = False,
        clean_after: int = 0,
    ) -> None:
        """
        Record a frame.

        Parameters
        ----------
        frame : NDArray[np.uint8]
            The frame.
        full : bool
            Whether a full refresh was asked for.
        turned : bool
            Whether it is upside down.
        quick : bool
            Ignored.
        asleep : bool
            Ignored.
        clear : bool
            Ignored.
        clean_after : int
            Ignored.
        """
        del quick, asleep, clear, clean_after
        self.frames.append((frame, full, turned))

    def settle(self, *, timeout: float) -> bool:
        """
        Report the display idle.

        Parameters
        ----------
        timeout : float
            Ignored.

        Returns
        -------
        bool
            Always True.
        """
        del timeout
        return True


@pytest.fixture(name='folder')
def _folder(tmp_path: Path) -> Path:
    """A screens folder holding the Sydney picture."""
    FRAME.tofile(tmp_path / 'loading-sydney.raw')
    return tmp_path


def test_loading_image_maps_the_app_key(folder: Path) -> None:
    """An app key names its city's picture."""
    assert loading_image(key='paperroute-sydney', folders=(folder,)) == (
        folder / 'loading-sydney.raw'
    )


@pytest.mark.parametrize(
    'key', ['paperroute-cambridge', 'sydney', 'other-sydney', ''],
)
def test_loading_image_none_for_a_missing_file(
    folder: Path, key: str,
) -> None:
    """A key with no picture, or not an app key, gives nothing."""
    assert loading_image(key=key, folders=(folder,)) is None


def test_load_draws_the_picture_and_holds_board_frames(folder: Path) -> None:
    """The picture goes up in full and board frames wait behind it."""
    shown = Shown()
    hold = LoadingHold(display=shown, folders=(folder,), turned=lambda: True,
    )
    hold.load(key='paperroute-sydney')
    hold.show(frame=BOARD)
    assert hold.settle(timeout=1)
    assert len(shown.frames) == 1
    frame, full, turned = shown.frames[0]
    assert (frame == FRAME).all()
    assert (full, turned) == (True, True)


def test_load_unknown_city_draws_nothing(folder: Path) -> None:
    """A key with no picture leaves board frames flowing."""
    shown = Shown()
    hold = LoadingHold(display=shown, folders=(folder,), turned=lambda: False,
    )
    hold.load(key='demo')
    hold.show(frame=BOARD, turned=True)
    assert [(full, turned) for _, full, turned in shown.frames] == [
        (False, True),
    ]


def test_release_redraws_the_last_board_frame(folder: Path) -> None:
    """After a refused switch the newest board frame comes back in full."""
    shown = Shown()
    hold = LoadingHold(display=shown, folders=(folder,), turned=lambda: False,
    )
    hold.show(frame=BOARD, turned=True)
    hold.load(key='paperroute-sydney')
    hold.show(frame=BOARD + 1, turned=True)
    hold.release()
    hold.show(frame=BOARD + 2)
    assert [
        (int(frame[0, 0]), full, turned)
        for frame, full, turned in shown.frames
    ] == [(0, False, True), (85, True, False), (1, True, True),
          (2, False, False)]


def test_release_without_a_picture_draws_nothing(folder: Path) -> None:
    """Releasing when nothing was held shows nothing."""
    shown = Shown()
    hold = LoadingHold(display=shown, folders=(folder,), turned=lambda: False,
    )
    hold.show(frame=BOARD)
    hold.release()
    assert len(shown.frames) == 1


def test_load_bad_file_raises_and_holds_nothing(tmp_path: Path) -> None:
    """A picture of the wrong size raises; board frames keep flowing."""
    (tmp_path / 'loading-sydney.raw').write_bytes(b'short')
    shown = Shown()
    hold = LoadingHold(display=shown, folders=(tmp_path,), turned=lambda: False,
    )
    with pytest.raises(ValueError):
        hold.load(key='paperroute-sydney')
    hold.show(frame=BOARD)
    assert len(shown.frames) == 1


def test_loading_image_prefers_the_first_folder(
    folder: Path, tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """The release's picture wins over the provisioned one."""
    first = tmp_path_factory.mktemp('release')
    FRAME.tofile(first / 'loading-sydney.raw')
    assert loading_image(
        key='paperroute-sydney', folders=(first, folder),
    ) == first / 'loading-sydney.raw'


def test_loading_image_falls_back_to_a_later_folder(
    folder: Path, tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """A release without the picture uses the provisioned one."""
    empty = tmp_path_factory.mktemp('release')
    assert loading_image(
        key='paperroute-sydney', folders=(empty, folder),
    ) == folder / 'loading-sydney.raw'


def test_load_missing_picture_warns_once(
    folder: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """A city with no picture logs one warning, however often it loads."""
    hold = LoadingHold(
        display=Shown(), folders=(folder,), turned=lambda: False,
    )
    with caplog.at_level(level=logging.WARNING):
        hold.load(key='paperroute-cambridge')
        hold.load(key='paperroute-cambridge')
        hold.load(key='demo')
        hold.load(key='paperroute-sydney')
    warned = [
        record.getMessage()
        for record in caplog.records
        if record.levelno == logging.WARNING
    ]
    assert len(warned) == 1
    assert 'loading-cambridge.raw' in warned[0]


def test_release_screens_sit_beside_the_venv() -> None:
    """The tree that holds the venv also holds the shipped pictures."""
    assert (RELEASE_SCREENS / 'loading-sydney.raw').is_file()
