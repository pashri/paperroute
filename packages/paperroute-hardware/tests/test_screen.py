"""Tests for showing one picture on the panel."""

from collections.abc import Callable
from pathlib import Path

import numpy as np
import pytest

from paperroute_device_testkit.hardware_fakes import FakePanelBus
from paperroute_device_testkit.panel_frames import drawn_planes, stepped_frame
from paperroute_hardware.adapters.epaper import PanelTimeout, Uc8179
from paperroute_hardware.adapters.panel_bus import LinuxPanelBus
from paperroute_hardware.render.panel_bits import (
    HEIGHT,
    WIDTH,
    grey_planes,
    oriented,
)
from paperroute_hardware.screen import load_frame, show_frame, show_image


def test_load_frame_reads_raw_grey(tmp_path: Path) -> None:
    """A raw file of the right size becomes a frame."""
    path = tmp_path / 'f.raw'
    path.write_bytes(bytes([85]) * (WIDTH * HEIGHT))
    frame = load_frame(path=path)
    assert frame.shape == (HEIGHT, WIDTH)
    assert frame.dtype == np.uint8
    assert int(frame[0, 0]) == 85


def test_load_frame_rejects_wrong_size(tmp_path: Path) -> None:
    """A file of the wrong size is refused."""
    path = tmp_path / 'f.raw'
    path.write_bytes(b'\x00' * 10)
    with pytest.raises(ValueError, match='bytes'):
        load_frame(path=path)


def test_show_frame_draws_full_then_closes() -> None:
    """One full refresh, then deep sleep, then power off."""
    bus = FakePanelBus()
    show_frame(
        panel=Uc8179(bus=bus),
        frame=np.full((HEIGHT, WIDTH), 255, dtype=np.uint8),
    )
    kinds = [kind for kind, _ in bus.log]
    assert kinds.count('close') == 1
    assert ('power', False) in bus.log
    assert bus.log.index(('power', False)) < bus.log.index(('close', None))
    assert bus.log[0] == ('power', True)


def test_show_frame_closes_after_timeout() -> None:
    """A panel that stays busy still powers off, and the error is raised."""
    bus = FakePanelBus(stuck=True)
    with pytest.raises(PanelTimeout):
        show_frame(
            panel=Uc8179(bus=bus, clock=lambda: bus.now_s),
            frame=stepped_frame(),
        )
    assert ('power', False) in bus.log
    assert bus.log[-1] == ('close', None)


def test_show_frame_raises_draw_error_when_close_fails() -> None:
    """A close that fails after a failed draw does not hide the draw error."""
    bus = FakePanelBus(stuck=True)
    panel = Uc8179(bus=bus, clock=lambda: bus.now_s)

    def broken_close() -> None:
        raise OSError('gone')

    bus.close = broken_close  # type: ignore[method-assign]
    with pytest.raises(PanelTimeout):
        show_frame(panel=panel, frame=stepped_frame())


@pytest.mark.parametrize('turned', [False, True])
def test_show_image_draws_the_picture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    turned: bool,
) -> None:
    """A good picture is drawn, turned when asked, and the panel closed."""
    bus = FakePanelBus()
    monkeypatch.setattr(LinuxPanelBus, 'open', staticmethod(lambda: bus))
    image = tmp_path / 'f.raw'
    image.write_bytes(stepped_frame().tobytes())
    assert show_image(image=image, turned=turned) == 0
    assert drawn_planes(bus=bus) == grey_planes(
        frame=oriented(frame=stepped_frame(), turned=turned),
    )
    assert bus.log[-1] == ('close', None)


def test_show_image_returns_1_on_bad_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    """A missing or wrong-sized picture is exit 1 with a message."""
    path = tmp_path / 'f.raw'
    path.write_bytes(b'\x00')
    assert show_image(image=path, turned=False) == 1
    assert capsys.readouterr().err.startswith('screen: ')
    missing = tmp_path / 'none.raw'
    assert show_image(image=missing, turned=False) == 1


def raiser(error: Exception) -> Callable[[], FakePanelBus]:
    """
    Make a stand-in for `LinuxPanelBus.open` that fails.

    Parameters
    ----------
    error : Exception
        What it raises.

    Returns
    -------
    Callable[[], FakePanelBus]
        The stand-in.
    """

    def fail() -> FakePanelBus:
        raise error

    return fail


@pytest.mark.parametrize(
    'error',
    [
        OSError('busy'),
        PanelTimeout('stuck'),
        ImportError('no gpiod'),
    ],
)
def test_show_image_returns_1_when_panel_unusable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    error: Exception,
) -> None:
    """A panel that cannot be opened is exit 1 with a message."""
    monkeypatch.setattr(
        LinuxPanelBus,
        'open',
        staticmethod(raiser(error)),
    )
    image = tmp_path / 'f.raw'
    image.write_bytes(stepped_frame().tobytes())
    assert show_image(image=image, turned=False) == 1
    assert capsys.readouterr().err == f'screen: {error}\n'
