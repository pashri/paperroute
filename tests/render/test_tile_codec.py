"""Tests for the tile codec and the plan B dithering."""

from typing import Final

import numpy as np
from numpy.typing import NDArray

from paperroute.render.tile_codec import (
    GREYS,
    diffuse,
    pack_bits,
    pack_grey,
    quantise4,
    unpack_bits,
    unpack_grey,
)

SIZE: Final[int] = 256
SMALL: Final[int] = 16
FIELD: Final[int] = 64
DARK: Final[int] = 85
BLANK_LIMIT: Final[int] = 100
INK_LOW: Final[float] = 0.60
INK_HIGH: Final[float] = 0.73


def _levels(*, size: int, seed: int) -> NDArray[np.uint8]:
    """Build a random square grid of the four grey levels."""
    rng = np.random.default_rng(seed)
    picks = rng.integers(0, len(GREYS), size=(size, size))
    return np.array(GREYS, dtype=np.uint8)[picks]


def _reference(*, grey: NDArray[np.uint8]) -> NDArray[np.bool_]:
    """Run a plain serial Floyd-Steinberg pass in float64."""
    height, width = grey.shape
    work = grey.astype(np.float64)
    paper = np.zeros((height, width), dtype=np.bool_)
    for y in range(height):
        for x in range(width):
            old = work[y, x]
            new = old >= 128
            err = old - 255.0 * new
            paper[y, x] = new
            if x + 1 < width:
                work[y, x + 1] += err * 7 / 16
            if y + 1 < height:
                if x > 0:
                    work[y + 1, x - 1] += err * 3 / 16
                work[y + 1, x] += err * 5 / 16
                if x + 1 < width:
                    work[y + 1, x + 1] += err / 16
    return paper


def test_quantise4_snaps_levels() -> None:
    """Values snap to the nearest of the four levels."""
    grey = np.array([[0, 42, 43, 127, 128, 212, 213, 255]], dtype=np.uint8)
    expected = [0, 0, 85, 85, 170, 170, 255, 255]
    assert quantise4(grey=grey).tolist() == [expected]


def test_pack_grey_round_trips() -> None:
    """A random four-level tile survives packing."""
    grey = _levels(size=SIZE, seed=1)
    blob = pack_grey(grey=grey)
    assert np.array_equal(unpack_grey(blob=blob), grey)


def test_blank_tile_packs_small() -> None:
    """An all-paper tile compresses to a few bytes."""
    grey = np.full((SIZE, SIZE), 255, dtype=np.uint8)
    assert len(pack_grey(grey=grey)) < BLANK_LIMIT


def test_diffuse_matches_reference_loop() -> None:
    """The wavefront gives exactly the serial result."""
    grey = _levels(size=SMALL, seed=2)
    assert np.array_equal(diffuse(grey=grey), _reference(grey=grey))


def test_diffuse_keeps_black_and_white() -> None:
    """Pure ink stays ink and pure paper stays paper."""
    ink = np.zeros((SMALL, SMALL), dtype=np.uint8)
    paper = np.full((SMALL, SMALL), 255, dtype=np.uint8)
    assert not diffuse(grey=ink).any()
    assert diffuse(grey=paper).all()


def test_diffuse_dark_grey_is_two_thirds_ink() -> None:
    """A field of dark grey dithers to about two thirds ink."""
    grey = np.full((FIELD, FIELD), DARK, dtype=np.uint8)
    ink = 1.0 - float(diffuse(grey=grey).mean())
    assert INK_LOW < ink < INK_HIGH


def test_pack_bits_round_trips() -> None:
    """Packed bits come back as 0 and 255."""
    rng = np.random.default_rng(3)
    paper = rng.integers(0, 2, size=(SIZE, SIZE)).astype(np.bool_)
    out = unpack_bits(blob=pack_bits(paper=paper))
    assert np.array_equal(out == 255, paper)
    assert set(np.unique(out).tolist()) <= {0, 255}
