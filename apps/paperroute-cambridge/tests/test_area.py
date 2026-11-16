"""Tests for the Cambridge map area."""

from paperroute_cambridge.area import CAMBRIDGE_BBOX


def test_bbox_holds_the_four_extreme_local_stops() -> None:
    """The padded box holds each extreme stop the coach box leaves out."""
    extremes = (
        (-0.47166, 52.13734),
        (0.48923, 52.09292),
        (0.22564, 52.01503),
        (0.29421, 52.46124),
    )
    for lon, lat in extremes:
        assert CAMBRIDGE_BBOX[0] <= lon <= CAMBRIDGE_BBOX[2]
        assert CAMBRIDGE_BBOX[1] <= lat <= CAMBRIDGE_BBOX[3]
