"""The aeroplane outline the canvas draws for the plane character."""

from typing import Final

import skia

HALF: Final[tuple[tuple[float, float], ...]] = (
    (0.5, 0.0), (0.58, 0.10), (0.585, 0.34), (1.0, 0.64), (1.0, 0.75),
    (0.585, 0.61), (0.57, 0.82), (0.74, 0.94), (0.74, 1.0), (0.5, 0.955),
)
PLANE_POINTS: Final[tuple[tuple[float, float], ...]] = HALF + tuple(
    (1 - x, y) for x, y in reversed(HALF[1:-1])
)
PLANE_MARGIN: Final[int] = 1  # px each side
PLANE_SCALE: Final[float] = 0.70 * 1.12  # of the type's pixel size


def plane_size(*, px: int) -> int:
    """
    Side of the pictogram's square for a type size.

    Parameters
    ----------
    px : int
        Type size in pixels.

    Returns
    -------
    int
        Side in pixels.
    """
    return round(px * PLANE_SCALE)


def plane_path(*, size: float, centre: tuple[float, float]) -> skia.Path:
    """
    The aeroplane outline, nose pointing right.

    Parameters
    ----------
    size : float
        Side of the square the outline fills, in pixels.
    centre : tuple[float, float]
        Centre of that square.

    Returns
    -------
    skia.Path
        Closed outline.
    """
    cx, cy = centre
    turned = [
        (cx - (y - 0.5) * size, cy + (x - 0.5) * size)
        for x, y in PLANE_POINTS
    ]
    result = skia.Path()
    result.moveTo(*turned[0])
    for point in turned[1:]:
        result.lineTo(*point)
    result.close()
    return result


def placed_plane(
    *,
    left: float,
    baseline: float,
    cap: float,
    px: int,
    snap: bool,
) -> skia.Path:
    """
    The outline placed in a line of text, centred on the cap height.

    Parameters
    ----------
    left : float
        Left edge of the pictogram's advance, margin included.
    baseline : float
        Text baseline.
    cap : float
        Cap height of the text.
    px : int
        Type size in pixels.
    snap : bool
        Round the centre so the edges fall on whole pixels.

    Returns
    -------
    skia.Path
        Closed outline.
    """
    size = plane_size(px=px)
    cx = left + PLANE_MARGIN + size / 2
    cy = baseline - cap / 2
    if snap:
        odd = 0.5 if size % 2 else 0.0
        cx, cy = round(cx - odd) + odd, round(cy - odd) + odd
    return plane_path(size=size, centre=(cx, cy))
