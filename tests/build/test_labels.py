"""Tests for global label placement."""

import itertools
import math
from collections.abc import Iterator, Sequence
from pathlib import Path

import numpy as np
import pytest

from paperroute.build.features import FeatureStore
from paperroute.build.labels import (
    LabelIndex,
    fit_street,
    label_box,
    place_labels,
)
from paperroute.build.osm_source import OsmPlace
from paperroute.build.tile_store import PlacedLabel
from paperroute.core.geo import Point, to_lonlat
from paperroute.core.tiles import (
    LabelKind,
    from_pixels,
    tiles_covering,
    to_pixels,
)

EDGE = 20480.0  # a block boundary at every zoom
MIDDLE = EDGE + 1024.0
ANCHOR = from_pixels(pixel=(MIDDLE, MIDDLE), mpp=3)
ZOOMS = (3, 5, 8, 14, 45)

Box = tuple[float, float, float, float]


@pytest.fixture(name='store')
def _store(tmp_path: Path) -> Iterator[FeatureStore]:
    """
    Open an empty scratch store.

    Parameters
    ----------
    tmp_path : Path
        Pytest's temporary directory.

    Yields
    ------
    FeatureStore
        The store.
    """
    store = FeatureStore(path=tmp_path / 'features.db')
    yield store
    store.conn.close()


def add_street(
    *,
    store: FeatureStore,
    pixels: Sequence[tuple[float, float]],
    mpp: int,
    name: str = 'Long Street',
) -> None:
    """
    Add a primary road drawn in a zoom's global pixels.

    Parameters
    ----------
    store : FeatureStore
        Target store.
    pixels : Sequence[tuple[float, float]]
        Vertices in global pixels.
    mpp : int
        Zoom the pixels are in.
    name : str
        Street name.
    """
    store.add_line(
        layer='road_primary',
        name=name,
        points=np.array([from_pixels(pixel=p, mpp=mpp) for p in pixels]),
    )


def add_place(*, store: FeatureStore, kind: str, name: str) -> None:
    """
    Add a place at the anchor.

    Parameters
    ----------
    store : FeatureStore
        Target store.
    kind : str
        Place kind, or ``station``.
    name : str
        The name.
    """
    lon, lat = to_lonlat(point=ANCHOR)
    store.add(
        feature=OsmPlace(kind=kind, name=name, rank=0, lon=lon, lat=lat),
    )


def around(*, box: Box) -> set[tuple[int, int]]:
    """
    List the tiles under a pixel box.

    Parameters
    ----------
    box : Box
        Left, top, right and bottom in global pixels.

    Returns
    -------
    set[tuple[int, int]]
        Column and row pairs.
    """
    left, top, right, bottom = box
    return set(tiles_covering(left=left, top=top, right=right, bottom=bottom))


def place(
    *,
    store: FeatureStore,
    zooms: Sequence[int],
    stops: Sequence[Point] = (),
) -> list[PlacedLabel]:
    """
    Place labels around the anchor.

    Parameters
    ----------
    store : FeatureStore
        The features.
    zooms : Sequence[int]
        Zooms to place.
    stops : Sequence[Point]
        Stops in metres.

    Returns
    -------
    list[PlacedLabel]
        Every placed label.
    """
    tiles: dict[int, set[tuple[int, int]]] = {}
    for mpp in zooms:
        x, y = to_pixels(point=ANCHOR, mpp=mpp)
        tiles[mpp] = around(box=(x - 3072, y - 1024, x + 3072, y + 1024))
    index = LabelIndex(conn=store.conn)
    return list(
        place_labels(store=store, tiles=tiles, stops=stops, index=index),
    )


def overlaps(*, first: Box, second: Box) -> bool:
    """
    Say whether two boxes overlap.

    Parameters
    ----------
    first : Box
        Left, top, right and bottom.
    second : Box
        Left, top, right and bottom.

    Returns
    -------
    bool
        True when they share area.
    """
    return (
        first[0] < second[2]
        and second[0] < first[2]
        and first[1] < second[3]
        and second[1] < first[3]
    )


def test_fit_street_rejects_a_curve() -> None:
    """A stretch bent through a right angle is not straight enough."""
    line = np.array([(0.0, 0.0), (50.0, 0.0), (50.0, 50.0)])
    label = fit_street(line=line, name='Bend', width=60, along=50, zoom=3)
    assert label is None


def test_fit_street_rejects_a_short_line() -> None:
    """The text must fit inside the line."""
    line = np.array([(0.0, 0.0), (50.0, 0.0)])
    label = fit_street(line=line, name='Short', width=60, along=25, zoom=3)
    assert label is None


@pytest.mark.parametrize(
    'line',
    [
        [(200.0, 100.0), (0.0, 110.0)],
        [(100.0, 200.0), (100.0, 0.0)],
        [(100.0, 0.0), (100.0, 200.0)],
        [(200.0, 200.0), (0.0, 0.0)],
    ],
)
def test_fit_street_turns_text_upright(
    line: list[tuple[float, float]],
) -> None:
    """Whatever way the street was drawn, the text reads upright."""
    label = fit_street(
        line=np.array(line),
        name='Upright',
        width=60,
        along=100,
        zoom=3,
    )
    assert label is not None
    assert -90 < label.angle <= 90


def test_fit_street_centre_is_whole_pixels() -> None:
    """The anchor is rounded; the ends are where the text starts and ends."""
    line = np.array([(0.3, 10.7), (200.3, 30.2)])
    label = fit_street(line=line, name='Round', width=60, along=100, zoom=3)
    assert label is not None
    assert (label.x, label.y) == (round(label.x), round(label.y))
    assert label.kind == LabelKind.STREET
    assert math.dist(label.start, label.end) == pytest.approx(60)
    centre = np.add(label.start, label.end) / 2
    assert math.dist(centre, (label.x, label.y)) <= math.sqrt(0.5)


def test_label_box_is_fourteen_pixels_tall() -> None:
    """A horizontal street's box is its width by 14 px."""
    line = np.array([(0.0, 0.0), (200.0, 0.0)])
    label = fit_street(line=line, name='Flat', width=60, along=100, zoom=3)
    assert label is not None
    assert label_box(label=label) == (70, -7, 130, 7)


def test_claim_refuses_overlap_and_accepts_apart(
    store: FeatureStore,
) -> None:
    """A box is taken only when nothing overlaps it."""
    index = LabelIndex(conn=store.conn)
    assert index.claim(box=(0, 0, 10, 10))
    assert not index.claim(box=(5, 5, 15, 15))
    assert index.claim(box=(20, 0, 30, 10))
    index.reserve(box=(40, 0, 50, 10))
    index.reserve(box=(45, 0, 55, 10))
    assert not index.claim(box=(52, 2, 60, 8))
    index.reset()
    assert index.claim(box=(5, 5, 15, 15))


def test_long_street_is_named_repeatedly(store: FeatureStore) -> None:
    """A straight 3,000 px street at 3 m/px is named again and again."""
    add_street(
        store=store,
        pixels=[(EDGE - 1000, MIDDLE), (EDGE + 2000, MIDDLE)],
        mpp=3,
    )
    labels = place(store=store, zooms=[3])
    assert len(labels) >= 6
    assert {label.name for label in labels} == {'Long Street'}


def test_label_never_sits_on_a_stop(store: FeatureStore) -> None:
    """No label at 3 m/px covers a stop's 24 px box."""
    add_street(
        store=store,
        pixels=[(MIDDLE - 192, MIDDLE), (MIDDLE + 900, MIDDLE)],
        mpp=3,
    )
    labels = place(store=store, zooms=[3], stops=[ANCHOR])
    stop = (MIDDLE - 12, MIDDLE - 12, MIDDLE + 12, MIDDLE + 12)
    assert labels
    assert not any(
        overlaps(first=label_box(label=label), second=stop)
        for label in labels
    )


def test_stops_are_not_reserved_at_city_zoom(store: FeatureStore) -> None:
    """At 45 m/px a town sitting on a stop is still named."""
    add_place(store=store, kind='town', name='Parramatta')
    labels = place(store=store, zooms=[45], stops=[ANCHOR])
    assert [label.name for label in labels] == ['P A R R A M A T T A']


def test_suburbs_only_from_8(store: FeatureStore) -> None:
    """A suburb is named at 8, 14 and 45 m/px only, in spaced capitals."""
    add_place(store=store, kind='suburb', name='Constitution Hill')
    labels = place(store=store, zooms=ZOOMS)
    assert sorted(label.zoom for label in labels) == [8, 14, 45]
    assert {label.name for label in labels} == {
        'C O N S T I T U T I O N   H I L L',
    }
    assert {label.kind for label in labels} == {LabelKind.SUBURB}


def test_city_zoom_names_suburbs_and_towns_only(
    store: FeatureStore,
) -> None:
    """A locality is named at 8 m/px but not at city zoom."""
    add_place(store=store, kind='locality', name='Old Toongabbie')
    labels = place(store=store, zooms=ZOOMS)
    zooms = {label.zoom for label in labels}
    assert 8 in zooms
    assert 45 not in zooms


def test_station_wins_over_suburb(store: FeatureStore) -> None:
    """A station and a suburb at one spot: only the station is named."""
    add_place(store=store, kind='suburb', name='Parramatta')
    add_place(store=store, kind='station', name='Parramatta')
    labels = place(store=store, zooms=[8])
    assert [label.kind for label in labels] == [LabelKind.STATION]
    label = labels[0]
    assert label.start == label.end
    x, y = to_pixels(point=ANCHOR, mpp=8)
    assert label.start == pytest.approx((x, y))
    assert (label.x, label.y) == (round(x), round(y - 15))


def test_station_rank_zero_comes_first(store: FeatureStore) -> None:
    """Of two stations at one spot, the full station is named."""
    lon, lat = to_lonlat(point=ANCHOR)
    for rank, name in ((1, 'Bus Stop'), (0, 'Rail Station')):
        store.add(
            feature=OsmPlace(
                kind='station',
                name=name,
                rank=rank,
                lon=lon,
                lat=lat,
            ),
        )
    labels = place(store=store, zooms=[8])
    assert [(label.name, label.rank) for label in labels] == [
        ('Rail Station', 0),
    ]


def test_block_edge_street_is_named_once(store: FeatureStore) -> None:
    """A street in pieces across a block edge is never named twice close."""
    for start in range(-1800, 1800, 400):
        add_street(
            store=store,
            pixels=[(EDGE + start, MIDDLE), (EDGE + start + 400, MIDDLE)],
            mpp=3,
        )
    labels = place(store=store, zooms=[3])
    assert len(labels) >= 6
    for first, second in itertools.combinations(labels, 2):
        assert math.dist((first.x, first.y), (second.x, second.y)) >= 300


def test_street_labels_stay_in_their_block(store: FeatureStore) -> None:
    """A street running out of the built block is named inside it only."""
    add_street(
        store=store,
        pixels=[(EDGE - 3000, MIDDLE), (EDGE + 5000, MIDDLE)],
        mpp=3,
    )
    tiles = {3: around(box=(EDGE, EDGE, EDGE + 2048, EDGE + 2048))}
    index = LabelIndex(conn=store.conn)
    labels = list(
        place_labels(store=store, tiles=tiles, stops=(), index=index),
    )
    assert len(labels) >= 4
    assert all(EDGE <= label.x < EDGE + 2048 for label in labels)


def test_label_belongs_to_the_block_holding_its_centre(
    store: FeatureStore,
) -> None:
    """A station just below the block is named; a suburb there is not."""
    for kind, x in (('station', MIDDLE), ('suburb', MIDDLE + 500)):
        below = from_pixels(pixel=(x, EDGE + 2048 + 10), mpp=8)
        lon, lat = to_lonlat(point=below)
        store.add(
            feature=OsmPlace(kind=kind, name='Edge', rank=0, lon=lon, lat=lat),
        )
    tiles = {8: around(box=(EDGE, EDGE, EDGE + 2048, EDGE + 2048))}
    index = LabelIndex(conn=store.conn)
    labels = list(
        place_labels(store=store, tiles=tiles, stops=(), index=index),
    )
    assert [(label.kind, label.y) for label in labels] == [
        (LabelKind.STATION, EDGE + 2048 - 5),
    ]
