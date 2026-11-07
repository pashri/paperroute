"""Every kind of screen draws today's pixels."""

import hashlib
import json
import sys
from pathlib import Path
from typing import Final, get_args

import numpy as np
import pytest
import skia
from numpy.typing import NDArray

from paperroute.core.messages import ScreenView
from paperroute.render.canvas import encode_png
from paperroute.render.panel_bits import grey_planes, oriented
from tests.golden.scenes import (
    GOLDEN,
    SCENES,
    UPDATING,
    baseline_bytes,
    frame_of,
    view_of,
)

FRAMES: Final[Path] = GOLDEN / 'frames' / sys.platform
PLANES: Final[Path] = GOLDEN / 'planes.json'
PLANE_SCENES: Final[tuple[str, ...]] = ('stop', 'map')


def decoded(*, data: bytes) -> NDArray[np.uint8]:
    """
    Read an 8-bit grey PNG.

    Parameters
    ----------
    data : bytes
        PNG file contents.

    Returns
    -------
    NDArray[np.uint8]
        Height by width grey levels.
    """
    image = skia.Image.MakeFromEncoded(data)
    pixels: NDArray[np.uint8] = image.toarray(
        colorType=skia.ColorType.kGray_8_ColorType,
    )
    return pixels


def mismatch(
    *, actual: NDArray[np.uint8], expected: NDArray[np.uint8], out: Path,
) -> str:
    """
    Save a frame that differs from its baseline, and the changed pixels.

    Parameters
    ----------
    actual : NDArray[np.uint8]
        The frame drawn today.
    expected : NDArray[np.uint8]
        The baseline.
    out : Path
        Directory for `actual.png` and `diff.png`.

    Returns
    -------
    str
        Where the files are and how many pixels changed.
    """
    changed = actual != expected
    diff = np.where(changed, 0, 255).astype(np.uint8)
    (out / 'actual.png').write_bytes(encode_png(grey=actual))
    (out / 'diff.png').write_bytes(encode_png(grey=diff))
    return f'{int(changed.sum())} pixels changed; see {out}'


def plane_hash(*, frame: NDArray[np.uint8], turned: bool) -> str:
    """
    Hash the bit planes the panel is sent for a frame.

    Parameters
    ----------
    frame : NDArray[np.uint8]
        The frame, right way up.
    turned : bool
        Whether the panel is mounted upside down.

    Returns
    -------
    str
        The sha256 of every plane's bytes, in order.
    """
    digest = hashlib.sha256()
    for plane in grey_planes(frame=oriented(frame=frame, turned=turned)):
        digest.update(plane)
    return digest.hexdigest()


@pytest.fixture(name='_frames')
def _frames_fixture() -> None:
    """Skip on a platform with no baseline frames."""
    if not UPDATING and not FRAMES.exists():
        pytest.skip(f'no golden frames for {sys.platform}')


def test_scenes_cover_every_screen_view() -> None:
    """Some scene shows each kind of screen."""
    shown = {type(view_of(state=scene())) for scene in SCENES.values()}
    assert shown == set(get_args(ScreenView))


def test_png_round_trip_is_exact() -> None:
    """Every grey survives encoding and decoding."""
    frame = np.tile(np.array([0, 85, 170, 255], np.uint8), (480, 200))
    assert np.array_equal(decoded(data=encode_png(grey=frame)), frame)


@pytest.mark.usefixtures('_frames')
@pytest.mark.parametrize('name', list(SCENES))
def test_frame_matches_baseline(name: str, tmp_path: Path) -> None:
    """The scene draws exactly its baseline frame."""
    actual = frame_of(state=SCENES[name]())
    path = FRAMES / f'{name}.png'
    expected = decoded(
        data=baseline_bytes(path=path, data=encode_png(grey=actual)),
    )
    assert np.array_equal(actual, expected), mismatch(
        actual=actual, expected=expected, out=tmp_path,
    )


def test_frame_is_repeatable() -> None:
    """Drawing a scene twice gives the same pixels."""
    for name, scene in SCENES.items():
        first, second = frame_of(state=scene()), frame_of(state=scene())
        assert np.array_equal(first, second), name


@pytest.mark.usefixtures('_frames')
def test_panel_planes_match_baseline() -> None:
    """The bit planes sent to the panel hash as they did, either way up."""
    hashes = {
        f'{name}/{"turned" if turned else "upright"}': plane_hash(
            frame=frame_of(state=SCENES[name]()), turned=turned,
        )
        for name in PLANE_SCENES
        for turned in (False, True)
    }
    text = json.dumps(hashes, indent=2, sort_keys=True) + '\n'
    stored = baseline_bytes(path=PLANES, data=text.encode())
    assert json.loads(stored) == hashes
