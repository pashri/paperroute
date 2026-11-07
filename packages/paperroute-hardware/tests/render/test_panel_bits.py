"""Tests for the frame-to-bytes helpers."""

import numpy as np
from numpy.typing import NDArray

from paperroute_hardware.render.panel_bits import (
    HEIGHT,
    WIDTH,
    Box,
    changed_box,
    grey_planes,
    mono_patch,
    oriented,
    quick_planes,
)


def _frame(*, value: int = 255) -> NDArray[np.uint8]:
    """
    Make a uniform frame.

    Parameters
    ----------
    value : int
        The grey of every pixel.

    Returns
    -------
    NDArray[np.uint8]
        The frame.
    """
    return np.full((HEIGHT, WIDTH), value, dtype=np.uint8)


def _bits(*, data: bytes, box: Box) -> int:
    """
    Count the set bits in a patch.

    Parameters
    ----------
    data : bytes
        The patch bytes.
    box : Box
        The patch's box.

    Returns
    -------
    int
        The number of set bits.
    """
    assert len(data) == box.width_bytes * (box.bottom - box.top)
    return int(np.unpackbits(np.frombuffer(data, dtype=np.uint8)).sum())


def test_grey_planes_codes() -> None:
    """Each grey maps to its pair of plane bits, leftmost pixel first."""
    frame = _frame()
    frame[0, :8] = (255, 170, 85, 0, 0, 85, 170, 255)
    plane_a, plane_b = grey_planes(frame=frame)
    assert len(plane_a) == len(plane_b) == 48000
    assert plane_a[0] == 0b01011010
    assert plane_b[0] == 0b00111100
    white_a, white_b = grey_planes(frame=_frame())
    assert not any(white_a)
    assert not any(white_b)


def test_grey_planes_rounds_to_nearest() -> None:
    """60 is dark and 200 is light."""
    frame = _frame()
    frame[0, 0] = 60
    frame[0, 1] = 200
    plane_a, plane_b = grey_planes(frame=frame)
    assert plane_a[0] == 0b01000000
    assert plane_b[0] == 0b10000000


def test_changed_box_none_when_equal() -> None:
    """Equal frames have no changed box."""
    assert changed_box(old=_frame(), new=_frame()) is None


def test_changed_box_aligns_to_bytes() -> None:
    """The box grows to whole bytes horizontally."""
    new = _frame()
    new[5, 13:15] = 0
    assert changed_box(old=_frame(), new=new) == Box(8, 5, 16, 6)


def test_mono_patch_extremes() -> None:
    """Black sets every bit and white sets none."""
    box = Box(0, 0, 16, 4)
    assert _bits(data=mono_patch(frame=_frame(value=0), box=box), box=box) == 64
    assert _bits(data=mono_patch(frame=_frame(), box=box), box=box) == 0


def test_mono_patch_grey_density() -> None:
    """Dark is 11/16 black and light is 5/16 black."""
    box = Box(0, 0, 16, 16)
    dark = mono_patch(frame=_frame(value=85), box=box)
    light = mono_patch(frame=_frame(value=170), box=box)
    assert _bits(data=dark, box=box) == 176
    assert _bits(data=light, box=box) == 80


def test_dither_is_anchored_to_screen() -> None:
    """Overlapping patches agree on their shared byte column."""
    frame = _frame(value=170)
    first = mono_patch(frame=frame, box=Box(0, 0, 16, 4))
    second = mono_patch(frame=frame, box=Box(8, 0, 24, 4))
    assert first[1::2] == second[0::2]


def test_oriented_turns_180() -> None:
    """A turned frame is rotated, and an unturned one is not."""
    frame = _frame()
    frame[0, 0] = 0
    turned = oriented(frame=frame, turned=True)
    assert turned[HEIGHT - 1, WIDTH - 1] == 0
    assert turned[0, 0] == 255
    assert (oriented(frame=frame, turned=False) == frame).all()


def test_grey_planes_of_box() -> None:
    """Box planes are slices of the whole-screen planes."""
    frame = _frame()
    frame[0, 8:16] = (255, 170, 85, 0, 0, 85, 170, 255)
    frame[1, 8:16] = 0
    whole = grey_planes(frame=frame)
    part = grey_planes(frame=frame, box=Box(8, 0, 16, 2))
    for full, small in zip(whole, part):
        assert small == bytes([full[1], full[101]])


def test_quick_planes_unchanged_pixels_match() -> None:
    """With no change the old plane equals the new plane."""
    frame = np.full((HEIGHT, WIDTH), 170, dtype=np.uint8)
    box = Box(left=0, top=0, right=16, bottom=2)
    old_bits, new_bits = quick_planes(old=frame, new=frame.copy(), box=box)
    assert old_bits == new_bits


def test_quick_planes_flips_only_changed_pixels() -> None:
    """Only the changed pixel differs between the planes."""
    old = np.full((HEIGHT, WIDTH), 255, dtype=np.uint8)
    new = old.copy()
    new[0, 3] = 0
    box = Box(left=0, top=0, right=8, bottom=1)
    old_bits, new_bits = quick_planes(old=old, new=new, box=box)
    assert new_bits == bytes([0b0001_0000])
    assert old_bits == bytes([0b0000_0000])


def test_quick_planes_grey_to_white_drives_pixel() -> None:
    """A grey pixel turning white is driven from black."""
    old = np.full((HEIGHT, WIDTH), 170, dtype=np.uint8)
    new = np.full((HEIGHT, WIDTH), 255, dtype=np.uint8)
    box = Box(left=0, top=0, right=8, bottom=4)
    old_bits, new_bits = quick_planes(old=old, new=new, box=box)
    assert new_bits == bytes(4)
    assert old_bits == bytes([0xFF]) * 4
