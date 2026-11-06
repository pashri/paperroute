"""Tests for the screen command."""

from pathlib import Path

import numpy as np
import pytest

from paperroute.adapters.epaper import Uc8179
from paperroute.render.panel_bits import HEIGHT, WIDTH
from paperroute.screen import load_frame, screen_main, show_frame
from tests.fakes import FakePanelBus


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


def test_screen_main_returns_1_on_bad_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    """A missing or wrong-sized picture is exit 1 with a message."""
    path = tmp_path / 'f.raw'
    path.write_bytes(b'\x00')
    assert screen_main(argv=['--image', str(path)]) == 1
    assert capsys.readouterr().err
    missing = tmp_path / 'none.raw'
    assert screen_main(argv=['--image', str(missing)]) == 1
