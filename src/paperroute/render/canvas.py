"""A small typed drawing surface over Skia, with no anti-aliasing."""

from collections.abc import Sequence
from dataclasses import dataclass
from functools import cache
from importlib.resources import files
from typing import Final

import numpy as np
import skia
from numpy.typing import NDArray

Point = tuple[float, float]
Box = tuple[float, float, float, float]
INK: Final[int] = 0
PAPER: Final[int] = 255
WIDTH: Final[int] = 800
HEIGHT: Final[int] = 480
ELLIPSIS: Final[str] = '...'


@dataclass(frozen=True)
class Face:
    """An Overpass weight at a pixel size."""

    weight: int
    px: int


@cache
def font(face: Face) -> skia.Font:
    """
    Load an aliased, fully hinted Overpass font.

    Parameters
    ----------
    face : Face
        Weight and size.

    Returns
    -------
    skia.Font
        The font.
    """
    ttf = (files('paperroute') / 'assets' / 'fonts'
           / f'Overpass-{face.weight}.ttf')
    loaded = skia.Font(skia.Typeface.MakeFromFile(str(ttf)), face.px)
    loaded.setEdging(skia.Font.Edging.kAlias)
    loaded.setHinting(skia.FontHinting.kFull)
    loaded.setSubpixel(False)
    return loaded


def paint(*, grey: int, stroke: float = 0) -> skia.Paint:
    """
    Make an aliased fill or stroke paint.

    Parameters
    ----------
    grey : int
        Grey level, 0 ink to 255 paper.
    stroke : float
        Stroke width; 0 fills.

    Returns
    -------
    skia.Paint
        The paint.
    """
    result = skia.Paint(Color=skia.Color(grey, grey, grey), AntiAlias=False)
    if stroke:
        result.setStyle(skia.Paint.kStroke_Style)
        result.setStrokeWidth(stroke)
        result.setStrokeJoin(skia.Paint.kRound_Join)
    return result


def path(*, points: Sequence[Point], close: bool) -> skia.Path:
    """
    Build a path through points.

    Parameters
    ----------
    points : Sequence[Point]
        Vertices.
    close : bool
        Close the path.

    Returns
    -------
    skia.Path
        The path.
    """
    result = skia.Path()
    result.moveTo(*points[0])
    for x, y in points[1:]:
        result.lineTo(x, y)
    if close:
        result.close()
    return result


class Canvas:
    """
    A grey drawing surface, paper white to start.

    Parameters
    ----------
    width : int
        Width in pixels.
    height : int
        Height in pixels.
    """

    def __init__(self, *, width: int = WIDTH, height: int = HEIGHT) -> None:
        self.surface = skia.Surface(width, height)
        self.canvas = self.surface.getCanvas()
        self.canvas.clear(skia.ColorWHITE)

    def rect(self, *, box: Box, grey: int) -> None:
        """
        Fill a box given as left, top, right, bottom.

        Parameters
        ----------
        box : Box
            Edges in pixels.
        grey : int
            Fill level.
        """
        self.canvas.drawRect(skia.Rect(*box), paint(grey=grey))

    def line(self, *, points: Sequence[Point], width: float,
             grey: int) -> None:
        """
        Stroke a polyline.

        Parameters
        ----------
        points : Sequence[Point]
            Vertices.
        width : float
            Stroke width.
        grey : int
            Stroke level.
        """
        self.canvas.drawPath(path(points=points, close=False),
                             paint(grey=grey, stroke=width))

    def polygon(self, *, points: Sequence[Point], grey: int) -> None:
        """
        Fill a polygon.

        Parameters
        ----------
        points : Sequence[Point]
            Vertices.
        grey : int
            Fill level.
        """
        self.canvas.drawPath(path(points=points, close=True),
                             paint(grey=grey))

    def text(self, *, at: Point, text: str, face: Face, grey: int,
             anchor: str = 'ls') -> float:
        """
        Draw text at an anchor and return its advance width.

        The anchor is two letters: l, m or r horizontally, then a (top of
        ascender), m (middle) or s (baseline) vertically.

        Parameters
        ----------
        at : Point
            Anchor position.
        text : str
            Text to draw.
        face : Face
            Font.
        grey : int
            Text level.
        anchor : str
            Two-letter anchor.

        Returns
        -------
        float
            Advance width in pixels.
        """
        loaded = font(face)
        width = float(loaded.measureText(text))
        metrics = loaded.getMetrics()
        dx = {'l': 0.0, 'm': -width / 2, 'r': -width}[anchor[0]]
        ascent, descent = -metrics.fAscent, metrics.fDescent
        dy = {'a': ascent, 'm': (ascent - descent) / 2, 's': 0.0}[anchor[1]]
        self.canvas.drawString(text, at[0] + dx, at[1] + dy, loaded,
                               paint(grey=grey))
        return width

    def length(self, *, text: str, face: Face) -> float:
        """
        Measure text.

        Parameters
        ----------
        text : str
            Text.
        face : Face
            Font.

        Returns
        -------
        float
            Advance width in pixels.
        """
        return float(font(face).measureText(text))

    def fit(self, *, text: str, face: Face, width: float) -> str:
        """
        Shorten text with a trailing ellipsis until it fits a width.

        Parameters
        ----------
        text : str
            Text.
        face : Face
            Font.
        width : float
            Available width in pixels.

        Returns
        -------
        str
            The text, or a shortened copy ending in '...'.
        """
        if self.length(text=text, face=face) <= width:
            return text
        cut = text
        while cut and self.length(text=cut + ELLIPSIS, face=face) > width:
            cut = cut[:-1]
        return cut.rstrip() + ELLIPSIS

    def grey(self) -> NDArray[np.uint8]:
        """
        Read the surface as grey pixels.

        Returns
        -------
        NDArray[np.uint8]
            Height by width grey levels.
        """
        rgba = self.surface.makeImageSnapshot().toarray()
        return np.ascontiguousarray(rgba[:, :, 1], dtype=np.uint8)


def encode_png(*, grey: NDArray[np.uint8]) -> bytes:
    """
    Encode grey pixels as an 8-bit greyscale PNG.

    Parameters
    ----------
    grey : NDArray[np.uint8]
        Height by width grey levels.

    Returns
    -------
    bytes
        PNG file contents.
    """
    image = skia.Image.fromarray(np.ascontiguousarray(grey),
                                 colorType=skia.ColorType.kGray_8_ColorType)
    return bytes(image.encodeToData(skia.EncodedImageFormat.kPNG, 100))
