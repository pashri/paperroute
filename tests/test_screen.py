"""Tests for the screen command."""

import json
from collections.abc import Callable
from pathlib import Path
from typing import cast

import numpy as np
import pytest
from numpy.typing import NDArray

from paperroute.adapters.epaper import PanelTimeout, Uc8179
from paperroute.adapters.panel_bus import LinuxPanelBus
from paperroute.render.panel_bits import HEIGHT, WIDTH, grey_planes, oriented
from paperroute.screen import load_frame, screen_main, show_frame
from paperroute_testkit.fakes import FakePanelBus


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


def stepped_frame() -> NDArray[np.uint8]:
    """
    Make a frame whose top-left corner differs from the rest.

    Returns
    -------
    NDArray[np.uint8]
        White, with a black block at the top left.
    """
    frame = np.full((HEIGHT, WIDTH), 255, dtype=np.uint8)
    frame[:40, :80] = 0
    return frame


def drawn_planes(*, bus: FakePanelBus) -> tuple[bytes, bytes]:
    """
    Get the last grey planes a bus was sent.

    Parameters
    ----------
    bus : FakePanelBus
        The bus.

    Returns
    -------
    tuple[bytes, bytes]
        The data of the last 0x10 and 0x13 commands.
    """
    sent = [
        cast(tuple[int, bytes], entry)
        for kind, entry in bus.log
        if kind == 'command'
    ]
    first = [data for code, data in sent if code == 0x10]
    second = [data for code, data in sent if code == 0x13]
    return first[-1], second[-1]


def run_screen(
    *,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    state: str | None,
) -> tuple[int, FakePanelBus]:
    """
    Run the command on a fake panel with the stepped frame.

    Parameters
    ----------
    tmp_path : Path
        Where to write the picture and the state file.
    monkeypatch : pytest.MonkeyPatch
        Swaps in the fake panel.
    state : str | None
        The state file's text, or None to pass no state file.

    Returns
    -------
    tuple[int, FakePanelBus]
        The exit status and the bus.
    """
    bus = FakePanelBus()
    monkeypatch.setattr(
        LinuxPanelBus,
        'open',
        staticmethod(lambda: bus),
    )
    image = tmp_path / 'f.raw'
    image.write_bytes(stepped_frame().tobytes())
    argv = ['--image', str(image)]
    if state is not None:
        path = tmp_path / 'state.json'
        path.write_text(state, encoding='utf-8')
        argv += ['--state', str(path)]
    return screen_main(argv=argv), bus


@pytest.mark.parametrize(
    ('state', 'turned'),
    [
        (None, False),
        (json.dumps({'settings': {'rotate': False}}), False),
        (json.dumps({'settings': {'rotate': True}}), True),
        ('{not json', False),
        (json.dumps({'settings': {'rotate': 'yes'}}), False),
        (json.dumps({'settings': []}), False),
        (json.dumps([1, 2]), False),
    ],
)
def test_screen_main_turns_frame_when_rotated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    state: str | None,
    turned: bool,
) -> None:
    """The picture is turned the way the app turns its frames."""
    status, bus = run_screen(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        state=state,
    )
    assert status == 0
    assert drawn_planes(bus=bus) == grey_planes(
        frame=oriented(frame=stepped_frame(), turned=turned),
    )
    assert bus.log[-1] == ('close', None)


def test_screen_main_draws_unturned_when_state_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A state file that cannot be read leaves the picture as stored."""
    bus = FakePanelBus()
    monkeypatch.setattr(
        LinuxPanelBus,
        'open',
        staticmethod(lambda: bus),
    )
    image = tmp_path / 'f.raw'
    image.write_bytes(stepped_frame().tobytes())
    argv = ['--image', str(image), '--state', str(tmp_path / 'none')]
    assert screen_main(argv=argv) == 0
    assert drawn_planes(bus=bus) == grey_planes(frame=stepped_frame())


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
def test_screen_main_returns_1_when_panel_unusable(
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
    assert screen_main(argv=['--image', str(image)]) == 1
    assert str(error) in capsys.readouterr().err


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


def test_screen_main_turns_frame_with_unreadable_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A state file that is not text leaves the picture as stored."""
    bus = FakePanelBus()
    monkeypatch.setattr(
        LinuxPanelBus,
        'open',
        staticmethod(lambda: bus),
    )
    image = tmp_path / 'f.raw'
    image.write_bytes(stepped_frame().tobytes())
    state = tmp_path / 'state.json'
    state.write_bytes(b'\xff\xfe{')
    argv = ['--image', str(image), '--state', str(state)]
    assert screen_main(argv=argv) == 0
    assert drawn_planes(bus=bus) == grey_planes(frame=stepped_frame())
