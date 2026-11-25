# /// script
# requires-python = ">=3.14"
# dependencies = ["numpy", "pillow"]
# ///
"""Draw the panel's OFF, ON and loading pictures as raw 800 x 480 frames.

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
CITIES: Final[tuple[str, ...]] = ('cambridge', 'sydney')
CITY_SCALE: Final[float] = 1.04  # city name width as a multiple of the panel
CITY_HEIGHT: Final[int] = 380  # px
CITY_TOP: Final[int] = 76  # px
LINE_TEXT: Final[str] = 'SWITCHING TO'  # sets the size of the LOADING letters
LINE_WIDTH: Final[int] = 520  # px, of LINE_TEXT
LINE_HEIGHT: Final[int] = 50  # px, of LINE_TEXT
LINE_ORIGIN: Final[tuple[int, int]] = (16, 12)  # left, top of LOADING, px
STRETCH_SIZE: Final[int] = 400  # pt, the size words are drawn at to stretch


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


def stretched(*, word: str, width: float, height: float) -> np.ndarray:
    """
    Draw a word stretched to fill a box exactly.

    Parameters
    ----------
    word : str
        Text to draw.
    width : float
        Width of the letters, in pixels.
    height : float
        Height of the letters, in pixels.

    Returns
    -------
    np.ndarray
        Boolean mask the size of the box, true inside the letters.
    """
    font = ImageFont.truetype(font=str(FONT), size=STRETCH_SIZE)
    left, top, right, bottom = font.getbbox(word, anchor='ls')
    img = Image.new(mode='L', size=(right - left, bottom - top), color=0)
    ImageDraw.Draw(img).text(
        xy=(-left, -top),
        text=word,
        font=font,
        fill=255,
        anchor='ls',
    )
    size = (int(width), int(height))
    return np.asarray(img.resize(size=size)) > 127


def placed(*, word: np.ndarray, left: int, top: int) -> np.ndarray:
    """
    Put a mask on the screen, cutting off whatever falls outside.

    Parameters
    ----------
    word : np.ndarray
        Boolean mask of the word.
    left : int
        Left edge of the mask, in pixels; may be negative.
    top : int
        Top edge of the mask, in pixels.

    Returns
    -------
    np.ndarray
        ``HEIGHT`` x ``WIDTH`` boolean mask.
    """
    pad = max(word.shape)
    big = np.zeros((HEIGHT + 2 * pad, WIDTH + 2 * pad), dtype=bool)
    rows, cols = word.shape
    big[top + pad:top + pad + rows, left + pad:left + pad + cols] = word
    return big[pad:pad + HEIGHT, pad:pad + WIDTH]


def loading_mask(*, city: str) -> np.ndarray:
    """
    Draw LOADING over the city's name, as on the switch mock-up.

    Parameters
    ----------
    city : str
        The city, in capitals.

    Returns
    -------
    np.ndarray
        Boolean mask of both words.
    """
    width = WIDTH * CITY_SCALE
    name = stretched(word=city, width=width, height=CITY_HEIGHT)
    font = ImageFont.truetype(font=str(FONT), size=STRETCH_SIZE)
    line = font.getbbox(LINE_TEXT, anchor='ls')
    word = font.getbbox('LOADING', anchor='ls')
    loading = stretched(
        word='LOADING',
        width=(word[2] - word[0]) * LINE_WIDTH / (line[2] - line[0]),
        height=(word[3] - word[1]) * LINE_HEIGHT / (line[3] - line[1]),
    )
    left, top = LINE_ORIGIN
    return placed(
        word=name, left=int(WIDTH / 2 - width / 2), top=CITY_TOP,
    ) | placed(word=loading, left=left, top=top)


def main() -> int:
    """
    Write ``off.raw``, ``on.raw`` and ``loading-<city>.raw`` to a folder.

    Each comes with a PNG preview.

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
    masks = [('off', off_mask()), ('on', on_mask())] + [
        (f'loading-{city}', loading_mask(city=city.upper()))
        for city in CITIES
    ]
    for name, word in masks:
        frame = np.where(word, sea, 0).astype(np.uint8)
        frame.tofile(out / f'{name}.raw')
        Image.fromarray(frame, mode='L').save(out / f'{name}-preview.png')
    return 0


if __name__ == '__main__':
    sys.exit(main())
