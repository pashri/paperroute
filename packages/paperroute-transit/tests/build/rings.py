"""Point-in-ring checks for geometry tests."""

from paperroute_transit.build.geometry import Ring


def inside(*, rings: list[Ring], point: tuple[float, float]) -> bool:
    """
    Test a point against rings with the even-odd rule.

    Parameters
    ----------
    rings : list[Ring]
        Closed rings.
    point : tuple[float, float]
        The point.

    Returns
    -------
    bool
        True when an odd number of rings hold the point.
    """
    px, py = point
    crossings = 0
    for ring in rings:
        for (x0, y0), (x1, y1) in zip(ring[:-1], ring[1:], strict=True):
            if (y0 > py) != (y1 > py):
                crossings += px < x0 + (py - y0) * (x1 - x0) / (y1 - y0)
    return crossings % 2 == 1
