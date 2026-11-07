"""Tests for the screen command."""

import json
from pathlib import Path

import pytest

from paperroute.screen import screen_main
from paperroute_hardware.adapters.panel_bus import LinuxPanelBus
from paperroute_hardware.render.panel_bits import grey_planes, oriented
from paperroute_testkit.hardware_fakes import FakePanelBus
from paperroute_testkit.panel_frames import drawn_planes, stepped_frame


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
