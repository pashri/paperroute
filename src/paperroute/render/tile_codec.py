"""Packing of map tiles and the plan B dithering."""

import zlib
from typing import Final

import numpy as np
from numpy.typing import NDArray

from paperroute.core.tiles import TILE

GREYS: Final[tuple[int, ...]] = (0, 85, 170, 255)
CUTS: Final[tuple[int, ...]] = (43, 128, 213)
LEVEL: Final[int] = 9
THRESHOLD: Final[int] = 128
PAPER_VALUE: Final[int] = 255
SHIFTS: Final[tuple[int, ...]] = (6, 4, 2, 0)


def quantise4(*, grey: NDArray[np.uint8]) -> NDArray[np.uint8]:
    """Snap each pixel to the nearest of the four grey levels.

    Parameters
    ----------
    grey : NDArray[np.uint8]
        Grey pixels, 0 to 255.

    Returns
    -------
    NDArray[np.uint8]
        The same shape, holding only the values in ``GREYS``.
    """
    index = np.digitize(grey, CUTS)
    return np.array(GREYS, dtype=np.uint8)[index]


def pack_grey(*, grey: NDArray[np.uint8]) -> bytes:
    """Pack a square grey tile as zlib over 2-bit levels.

    Four pixels go in each byte, the first in the high bits.

    Parameters
    ----------
    grey : NDArray[np.uint8]
        A square tile whose pixels are the values in ``GREYS``.

    Returns
    -------
    bytes
        The compressed tile.
    """
    index = np.digitize(grey, CUTS).astype(np.uint8).reshape(-1, 4)
    packed = np.zeros(index.shape[0], dtype=np.uint8)
    for column, shift in enumerate(SHIFTS):
        packed |= index[:, column] << shift
    return zlib.compress(packed.tobytes(), LEVEL)


def unpack_grey(*, blob: bytes, size: int = TILE) -> NDArray[np.uint8]:
    """Unpack a tile written by ``pack_grey``.

    Parameters
    ----------
    blob : bytes
        The compressed tile.
    size : int
        The tile's side in pixels.

    Returns
    -------
    NDArray[np.uint8]
        A ``size`` by ``size`` array of grey values.
    """
    packed = np.frombuffer(zlib.decompress(blob), dtype=np.uint8)
    index = np.stack([(packed >> shift) & 3 for shift in SHIFTS], axis=1)
    return np.array(GREYS, dtype=np.uint8)[index].reshape(size, size)


def pack_bits(*, paper: NDArray[np.bool_]) -> bytes:
    """Pack a black and white tile as zlib over bits, 1 for paper.

    Parameters
    ----------
    paper : NDArray[np.bool_]
        True where the pixel is paper.

    Returns
    -------
    bytes
        The compressed tile.
    """
    return zlib.compress(np.packbits(paper).tobytes(), LEVEL)


def unpack_bits(*, blob: bytes, size: int = TILE) -> NDArray[np.uint8]:
    """Unpack a tile written by ``pack_bits``.

    Parameters
    ----------
    blob : bytes
        The compressed tile.
    size : int
        The tile's side in pixels.

    Returns
    -------
    NDArray[np.uint8]
        A ``size`` by ``size`` array of 0 (ink) and 255 (paper).
    """
    packed = np.frombuffer(zlib.decompress(blob), dtype=np.uint8)
    bits = np.unpackbits(packed)[: size * size]
    return (bits * PAPER_VALUE).astype(np.uint8).reshape(size, size)


def _spread(
    *,
    work: NDArray[np.float64],
    ys: NDArray[np.intp],
    xs: NDArray[np.intp],
    err: NDArray[np.float64],
) -> None:
    """Add one wave's error shares to the pixels that follow it.

    Parameters
    ----------
    work : NDArray[np.float64]
        The tile being dithered, updated in place.
    ys : NDArray[np.intp]
        Rows of the wave's pixels.
    xs : NDArray[np.intp]
        Columns of the wave's pixels.
    err : NDArray[np.float64]
        Each pixel's quantisation error.
    """
    height, width = work.shape
    shares = ((1, -1, 3), (1, 0, 5), (1, 1, 1), (0, 1, 7))
    for dy, dx, weight in shares:
        ty, tx = ys + dy, xs + dx
        keep = (ty < height) & (tx >= 0) & (tx < width)
        part = err[keep] * weight / 16
        work[ty[keep], tx[keep]] += part


def diffuse(*, grey: NDArray[np.uint8]) -> NDArray[np.bool_]:
    """Dither a grey tile to black and white with Floyd-Steinberg.

    The result equals the usual row by row pass exactly. Pixels with the
    same ``x + 2y`` never feed each other, so each such wave is one numpy
    step. The down-left share is added before the right share so every
    pixel sums its errors in the row by row order.

    Parameters
    ----------
    grey : NDArray[np.uint8]
        Grey pixels, 0 to 255.

    Returns
    -------
    NDArray[np.bool_]
        True where the pixel is paper.
    """
    height, width = grey.shape
    work = grey.astype(np.float64)
    paper = np.zeros((height, width), dtype=np.bool_)
    for wave in range(width + 2 * (height - 1)):
        low = max(0, -(-(wave - width + 1) // 2))
        ys = np.arange(low, min(height - 1, wave // 2) + 1)
        xs = wave - 2 * ys
        old = work[ys, xs]
        new = old >= THRESHOLD
        paper[ys, xs] = new
        _spread(work=work, ys=ys, xs=xs, err=old - PAPER_VALUE * new)
    return paper
