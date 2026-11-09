# /// script
# requires-python = ">=3.14"
# dependencies = ["numpy", "pillow"]
# ///
"""Draw the panel's OFF and ON pictures as raw 800 x 480 grey frames.

Run from anywhere with ``uv run tools/screens/make_screens.py OUT_DIR``.
Each word is cut out of the dithered sea photo; the rest is black. The
frames hold only the panel's four greys (0, 85, 170, 255).
"""

import sys
from pathlib import Path
from typing import Final

import numpy as np
from PIL import Image, ImageDraw, ImageFont

WIDTH: Final[int] = 800  # px
HEIGHT: Final[int] = 480  # px
HERE: Final[Path] = Path(__file__).parent
FONT: Final[Path] = HERE / 'BarlowCondensed-Black.ttf'
SEA: Final[Path] = HERE / 'sea.png'
GREY_STEP: Final[int] = 85
OFF_SCALE: Final[float] = 1.2  # letter width as a multiple of the panel
ON_CAP: Final[float] = 580  # px from baseline to cap top
ON_ORIGIN: Final[tuple[int, int]] = (-8, 470)  # baseline start, px
REFERENCE_SIZE: Final[int] = 100  # pt, used to measure the font


def mask(*, word: str, size: float, x: float, y: float) -> np.ndarray:
    """
    Draw a word as a boolean mask.

    Parameters
    ----------
    word : str
        Text to draw.
    size : float
        Font size in points.
    x : float
        Left edge of the baseline, in pixels.
    y : float
        Baseline, in pixels from the top.

    Returns
    -------
    np.ndarray
        ``HEIGHT`` x ``WIDTH`` array, true inside the letters.
    """
    img = Image.new(mode='L', size=(WIDTH, HEIGHT), color=0)
    font = ImageFont.truetype(font=str(FONT), size=round(size))
    ImageDraw.Draw(img).text(
        xy=(x, y),
        text=word,
        font=font,
        fill=255,
        anchor='ls',
    )
    return np.asarray(img) >= 128


def off_mask() -> np.ndarray:
    """
    Draw OFF centred, ``OFF_SCALE`` times the panel's width.

    Returns
    -------
    np.ndarray
        Boolean mask of the word.
    """
    probe = ImageFont.truetype(font=str(FONT), size=1000)
    left, _, right, _ = probe.getbbox('OFF', anchor='ls')
    size = 1000 * OFF_SCALE * WIDTH / (right - left)
    font = ImageFont.truetype(font=str(FONT), size=round(size))
    left, top, right, bottom = font.getbbox('OFF', anchor='ls')
    return mask(
        word='OFF',
        size=size,
        x=(WIDTH - (right - left)) / 2 - left,
        y=(HEIGHT - (bottom - top)) / 2 - top,
    )


def on_mask() -> np.ndarray:
    """
    Draw ON with its cap height ``ON_CAP``, from ``ON_ORIGIN``.

    Returns
    -------
    np.ndarray
        Boolean mask of the word.
    """
    probe = ImageFont.truetype(font=str(FONT), size=REFERENCE_SIZE)
    ascent = -probe.getbbox('OFF', anchor='ls')[1]
    x, y = ON_ORIGIN
    return mask(
        word='ON',
        size=ON_CAP / ascent * REFERENCE_SIZE,
        x=x,
        y=y,
    )


def main() -> int:
    """
    Write ``off.raw`` and ``on.raw``, with PNG previews, to the given folder.

    Returns
    -------
    int
        Exit code: 0 when written, 2 for a usage error.
    """
    if len(sys.argv) != 2:
        print('usage: make_screens.py OUT_DIR', file=sys.stderr)
        return 2
    out = Path(sys.argv[1])
    sea = np.asarray(Image.open(SEA)).astype(np.uint8) * GREY_STEP
    for name, word in (('off', off_mask()), ('on', on_mask())):
        frame = np.where(word, sea, 0).astype(np.uint8)
        frame.tofile(out / f'{name}.raw')
        Image.fromarray(frame, mode='L').save(out / f'{name}-preview.png')
    return 0


if __name__ == '__main__':
    sys.exit(main())
