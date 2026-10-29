"""A small typed drawing surface over Skia, aliased unless asked."""

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
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
    """An Overpass weight at a pixel size.

    Attributes
    ----------
    weight : int
        Font weight.
    px : int
        Pixel size.
    squeeze : float
        Horizontal scale; 1 is the font's own width.
    """

    weight: int
    px: int
    squeeze: float = 1.0


@cache
def font(face: Face, smooth: bool = False) -> skia.Font:
    """
    Load a fully hinted Overpass font.

    Parameters
    ----------
    face : Face
        Weight and size.
    smooth : bool
        Anti-alias the glyph edges; False keeps them aliased.

    Returns
    -------
    skia.Font
        The font.
    """
    ttf = (files('paperroute') / 'assets' / 'fonts'
           / f'Overpass-{face.weight}.ttf')
    loaded = skia.Font(
        skia.Typeface.MakeFromFile(str(ttf)), face.px, face.squeeze, 0,
    )
    loaded.setEdging(
        skia.Font.Edging.kAntiAlias if smooth else skia.Font.Edging.kAlias,
    )
    loaded.setHinting(skia.FontHinting.kFull)
    loaded.setSubpixel(False)
    return loaded


def paint(
    *,
    grey: int,
    stroke: float = 0,
    smooth: bool = False,
) -> skia.Paint:
    """
    Make a fill or stroke paint.

    Parameters
    ----------
    grey : int
        Grey level, 0 ink to 255 paper.
    stroke : float
        Stroke width; 0 fills.
    smooth : bool
        Anti-alias the edges; False keeps them aliased.

    Returns
    -------
    skia.Paint
        The paint.
    """
    result = skia.Paint(
        Color=skia.Color(grey, grey, grey),
        AntiAlias=smooth,
    )
    if stroke:
        result.setStyle(skia.Paint.kStroke_Style)
        result.setStrokeWidth(stroke)
        result.setStrokeJoin(skia.Paint.kRound_Join)
    return result


def text_width(*, text: str, face: Face) -> float:
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


def anchor_offset(*, face: Face, width: float, anchor: str) -> Point:
    """
    Offset from an anchor to the text baseline origin.

    Parameters
    ----------
    face : Face
        Font.
    width : float
        Advance width of the text.
    anchor : str
        Two-letter anchor.

    Returns
    -------
    Point
        Horizontal and vertical offset to add to the anchor.
    """
    metrics = font(face).getMetrics()
    dx = {'l': 0.0, 'm': -width / 2, 'r': -width}[anchor[0]]
    ascent, descent = -metrics.fAscent, metrics.fDescent
    dy = {'a': ascent, 'm': (ascent - descent) / 2, 's': 0.0}[anchor[1]]
    return dx, dy


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


def box_mean(*, grey: NDArray[np.uint8], factor: int) -> NDArray[np.uint8]:
    """
    Shrink pixels by averaging each square block, rounded to the nearest.

    Parameters
    ----------
    grey : NDArray[np.uint8]
        Height by width grey levels, both multiples of ``factor``.
    factor : int
        Block side in pixels.

    Returns
    -------
    NDArray[np.uint8]
        Height and width divided by ``factor``.
    """
    height, width = grey.shape[0] // factor, grey.shape[1] // factor
    mean = (
        grey.astype(np.float64)
        .reshape(height, factor, width, factor)
        .mean(axis=(1, 3))
    )
    return np.clip(np.rint(mean), 0, 255).astype(np.uint8)


class Canvas:
    """
    A grey drawing surface, paper white to start.

    Drawing coordinates are always in the canvas's own pixels. With a
    ``scale`` above 1 the surface holds that many sub-pixels each way,
    and ``grey`` averages them back down.

    Parameters
    ----------
    width : int
        Width in pixels.
    height : int
        Height in pixels.
    scale : int
        Sub-pixels per pixel along each side.
    smooth : bool
        Anti-alias shapes and text; False keeps every edge aliased.
    """

    def __init__(
        self,
        *,
        width: int = WIDTH,
        height: int = HEIGHT,
        scale: int = 1,
        smooth: bool = False,
    ) -> None:
        self.scale = scale
        self.smooth = smooth
        self.surface = skia.Surface(width * scale, height * scale)
        self.canvas = self.surface.getCanvas()
        self.canvas.clear(skia.ColorWHITE)
        self.canvas.scale(scale, scale)

    def paint(self, *, grey: int, stroke: float = 0) -> skia.Paint:
        """
        Make a paint that follows the canvas's smoothing.

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
        return paint(grey=grey, stroke=stroke, smooth=self.smooth)

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
        self.canvas.drawRect(skia.Rect(*box), self.paint(grey=grey))

    def invert(self, *, box: Box) -> None:
        """
        Swap ink and paper inside a box given as left, top, right, bottom.

        Parameters
        ----------
        box : Box
            Edges in pixels.
        """
        flip = self.paint(grey=PAPER)
        flip.setBlendMode(skia.BlendMode.kDifference)
        self.canvas.drawRect(skia.Rect(*box), flip)

    def checker(self, *, box: Box, grey: int) -> None:
        """
        Cover every other pixel of a box, in a checkerboard.

        The pixel at the box's top left corner is covered.

        Parameters
        ----------
        box : Box
            Left, top, right, bottom, in whole pixels.
        grey : int
            Level of the covered pixels.
        """
        left, top, right, bottom = (int(edge) for edge in box)
        squares = skia.Path()
        for y in range(top, bottom):
            for x in range(left + (y - top) % 2, right, 2):
                squares.addRect(skia.Rect(x, y, x + 1, y + 1))
        self.canvas.drawPath(squares, self.paint(grey=grey))

    def line(self, *, points: Sequence[Point], width: float, grey: int,
             dash: tuple[float, float] | None = None) -> None:
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
        dash : tuple[float, float] | None
            Dash and gap lengths; None strokes solid.
        """
        stroke = self.paint(grey=grey, stroke=width)
        if dash:
            stroke.setPathEffect(skia.DashPathEffect.Make(list(dash), 0))
        self.canvas.drawPath(path(points=points, close=False), stroke)

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
                             self.paint(grey=grey))

    def text(self, *, at: Point, text: str, face: Face, grey: int,
             anchor: str = 'ls', halo: int = 0,
             angle: float = 0.0) -> float:
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
        halo : int
            Width of the paper outline around the glyphs; 0 for none.
        angle : float
            Rotation about the anchor in degrees, clockwise on screen.

        Returns
        -------
        float
            Advance width in pixels.
        """
        loaded = font(face, smooth=self.smooth)
        width = text_width(text=text, face=face)
        dx, dy = anchor_offset(face=face, width=width, anchor=anchor)
        with self.rotated(at=at, angle=angle):
            x, y = (dx, dy) if angle else (at[0] + dx, at[1] + dy)
            self.stroke_halo(text=text, at=(x, y), font_=loaded, halo=halo)
            self.canvas.drawString(text, x, y, loaded, self.paint(grey=grey))
        return width

    def stroke_halo(self, *, text: str, at: Point, font_: skia.Font,
             halo: int) -> None:
        """
        Stroke text in paper so the glyphs stand out from what is below.

        Parameters
        ----------
        text : str
            Text to outline.
        at : Point
            Baseline origin.
        font_ : skia.Font
            Font.
        halo : int
            Outline width on each side; 0 draws nothing.
        """
        if halo:
            outline = self.paint(grey=PAPER, stroke=2 * halo)
            self.canvas.drawString(text, at[0], at[1], font_, outline)

    @contextmanager
    def rotated(self, *, at: Point, angle: float) -> Iterator[None]:
        """
        Rotate drawing clockwise about a point.

        Parameters
        ----------
        at : Point
            Pivot.
        angle : float
            Degrees clockwise on screen; 0 changes nothing.

        Yields
        ------
        None
            Drawing inside the block is rotated.
        """
        self.canvas.save()
        if angle:
            self.canvas.translate(*at)
            self.canvas.rotate(angle)
        try:
            yield
        finally:
            self.canvas.restore()

    @contextmanager
    def clipped(self, *, box: Box) -> Iterator[None]:
        """
        Restrict drawing to a box.

        Parameters
        ----------
        box : Box
            Left, top, right, bottom.

        Yields
        ------
        None
            Drawing inside the block is clipped.
        """
        self.canvas.save()
        self.canvas.clipRect(skia.Rect(*box))
        try:
            yield
        finally:
            self.canvas.restore()

    def ellipse(self, *, box: Box, grey: int, stroke: float = 0) -> None:
        """
        Draw an ellipse inside a box.

        Parameters
        ----------
        box : Box
            Left, top, right, bottom.
        grey : int
            Level.
        stroke : float
            Outline width; 0 fills.
        """
        self.canvas.drawOval(
            skia.Rect(*box), self.paint(grey=grey, stroke=stroke))

    def rings(self, *, rings: Sequence[Sequence[Point]], grey: int) -> None:
        """
        Fill closed rings with the even-odd rule, so holes stay empty.

        Parameters
        ----------
        rings : Sequence[Sequence[Point]]
            Closed outlines.
        grey : int
            Fill level.
        """
        combined = skia.Path()
        combined.setFillType(skia.PathFillType.kEvenOdd)
        for ring in rings:
            combined.addPath(path(points=ring, close=True))
        self.canvas.drawPath(combined, self.paint(grey=grey))

    def polygons(
        self, *, polygons: Sequence[Sequence[Point]], grey: int,
    ) -> None:
        """
        Fill polygons in one call, so overlaps stay filled.

        Parameters
        ----------
        polygons : Sequence[Sequence[Point]]
            Closed outlines, all turning the same way.
        grey : int
            Fill level.
        """
        combined = skia.Path()
        combined.setFillType(skia.PathFillType.kWinding)
        for points in polygons:
            combined.addPath(path(points=points, close=True))
        self.canvas.drawPath(combined, self.paint(grey=grey))

    def image(self, *, grey: NDArray[np.uint8], at: Point) -> None:
        """
        Blit grey pixels with their top left corner at a point.

        Parameters
        ----------
        grey : NDArray[np.uint8]
            Height by width grey levels.
        at : Point
            Top left corner.
        """
        picture = skia.Image.fromarray(
            np.ascontiguousarray(grey),
            colorType=skia.ColorType.kGray_8_ColorType,
        )
        self.canvas.drawImage(picture, at[0], at[1])

    def dots(self, *, points: Sequence[Point], diameter: float,
             grey: int) -> None:
        """
        Draw round dots in one call.

        Parameters
        ----------
        points : Sequence[Point]
            Dot centres.
        diameter : float
            Dot width in pixels.
        grey : int
            Level.
        """
        brush = self.paint(grey=grey, stroke=diameter)
        brush.setStrokeCap(skia.Paint.kRound_Cap)
        self.canvas.drawPoints(
            skia.Canvas.kPoints_PointMode,
            [skia.Point(x, y) for x, y in points],
            brush,
        )

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
        return text_width(text=text, face=face)

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
            The text, or a shortened copy ending in '...', or '' when
            not even that fits.
        """
        if self.length(text=text, face=face) <= width:
            return text
        if self.length(text=ELLIPSIS, face=face) > width:
            return ''
        cut = text
        while cut and self.length(text=cut + ELLIPSIS, face=face) > width:
            cut = cut[:-1]
        return cut.rstrip() + ELLIPSIS

    def grey(self) -> NDArray[np.uint8]:
        """
        Read the surface as grey pixels, each the mean of its sub-pixels.

        Returns
        -------
        NDArray[np.uint8]
            Height by width grey levels.
        """
        rgba = self.surface.makeImageSnapshot().toarray()
        grey = np.ascontiguousarray(rgba[:, :, 1], dtype=np.uint8)
        if self.scale == 1:
            return grey
        return box_mean(grey=grey, factor=self.scale)


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
