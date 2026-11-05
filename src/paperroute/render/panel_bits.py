"""Turn grey frames into the bytes an e-paper controller takes."""

from dataclasses import dataclass
from typing import Final

import numpy as np
from numpy.typing import NDArray

WIDTH: Final[int] = 800
HEIGHT: Final[int] = 480
BAYER4: Final[NDArray[np.uint16]] = np.array(
    (
        (0, 8, 2, 10),
        (12, 4, 14, 6),
        (3, 11, 1, 9),
        (15, 7, 13, 5),
    ),
    dtype=np.uint16,
)
BYTE: Final[int] = 8  # pixels


@dataclass(frozen=True)
class Box:
    """
    A half-open pixel rectangle whose left and right are byte aligned.

    Attributes
    ----------
    left : int
        First column; a multiple of 8.
    top : int
        First row.
    right : int
        Column after the last; a multiple of 8.
    bottom : int
        Row after the last.
    """

    left: int
    top: int
    right: int
    bottom: int

    @property
    def width_bytes(self) -> int:
        """
        Get the box's width in bytes.

        Returns
        -------
        int
            The number of bytes in one row of the box.
        """
        return (self.right - self.left) // BYTE


def oriented(
    *,
    frame: NDArray[np.uint8],
    turned: bool,
) -> NDArray[np.uint8]:
    """
    Turn a frame half way round when the panel is mounted upside down.

    Parameters
    ----------
    frame : NDArray[np.uint8]
        The frame, right way up.
    turned : bool
        Whether to turn it 180 degrees.

    Returns
    -------
    NDArray[np.uint8]
        The frame to send.
    """
    return frame[::-1, ::-1] if turned else frame


def crop(
    *,
    frame: NDArray[np.uint8],
    box: Box | None,
) -> NDArray[np.uint8]:
    """
    Cut a box out of a frame.

    Parameters
    ----------
    frame : NDArray[np.uint8]
        The frame.
    box : Box | None
        The area to keep, or None for all of it.

    Returns
    -------
    NDArray[np.uint8]
        The pixels in the box.
    """
    if box is None:
        return frame
    return frame[box.top : box.bottom, box.left : box.right]


def grey_levels(*, pixels: NDArray[np.uint8]) -> NDArray[np.uint16]:
    """
    Round pixels to the nearest of the four greys.

    Parameters
    ----------
    pixels : NDArray[np.uint8]
        Grey values.

    Returns
    -------
    NDArray[np.uint16]
        0 for black, 1 dark, 2 light and 3 white.
    """
    return (pixels.astype(np.uint16) + 42) // 85


def grey_planes(
    *,
    frame: NDArray[np.uint8],
    box: Box | None = None,
) -> tuple[bytes, bytes]:
    """
    Split a frame into the two bit planes of the grey waveform.

    Parameters
    ----------
    frame : NDArray[np.uint8]
        The frame.
    box : Box | None
        The area to take, or None for the whole screen.

    Returns
    -------
    tuple[bytes, bytes]
        The planes for commands 0x10 and 0x13.
    """
    levels = grey_levels(pixels=crop(frame=frame, box=box))
    plane_a = levels % 2 == 0
    plane_b = levels < 2
    return (
        np.packbits(plane_a, axis=1).tobytes(),
        np.packbits(plane_b, axis=1).tobytes(),
    )


def has_grey(*, frame: NDArray[np.uint8], box: Box) -> bool:
    """
    Tell whether any pixel in a box is dark or light grey.

    Parameters
    ----------
    frame : NDArray[np.uint8]
        The frame.
    box : Box
        The area to look at.

    Returns
    -------
    bool
        True when a pixel rounds to dark or light.
    """
    levels = grey_levels(pixels=crop(frame=frame, box=box))
    return bool(((levels == 1) | (levels == 2)).any())


def union(*, a: Box | None, b: Box) -> Box:
    """
    Find the smallest box covering two boxes.

    Parameters
    ----------
    a : Box | None
        The first box, or None.
    b : Box
        The second box.

    Returns
    -------
    Box
        The covering box.
    """
    if a is None:
        return b
    return Box(
        left=min(a.left, b.left),
        top=min(a.top, b.top),
        right=max(a.right, b.right),
        bottom=max(a.bottom, b.bottom),
    )


def changed_box(
    *,
    old: NDArray[np.uint8],
    new: NDArray[np.uint8],
) -> Box | None:
    """
    Find the byte-aligned box around the pixels that differ.

    Parameters
    ----------
    old : NDArray[np.uint8]
        The earlier frame.
    new : NDArray[np.uint8]
        The later frame.

    Returns
    -------
    Box | None
        The box, or None when the frames are equal.
    """
    rows, cols = np.nonzero(old != new)
    if rows.size == 0:
        return None
    return Box(
        left=int(cols.min()) // BYTE * BYTE,
        top=int(rows.min()),
        right=-(-(int(cols.max()) + 1) // BYTE) * BYTE,
        bottom=int(rows.max()) + 1,
    )


def mono_patch(*, frame: NDArray[np.uint8], box: Box) -> bytes:
    """
    Dither a box to 1 bit with a screen-anchored ordered 4x4 pattern.

    Parameters
    ----------
    frame : NDArray[np.uint8]
        The frame.
    box : Box
        The area to take.

    Returns
    -------
    bytes
        Rows of bits, 1 for black, `box.width_bytes` bytes per row.
    """
    ys = np.arange(box.top, box.bottom)[:, None] % 4
    xs = np.arange(box.left, box.right)[None, :] % 4
    limits = (BAYER4[ys, xs] * 16 + 8).astype(np.uint16)
    pixels = crop(frame=frame, box=box).astype(np.uint16)
    return np.packbits(pixels < limits, axis=1).tobytes()
