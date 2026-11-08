"""Tests for global label placement."""

import itertools
import math
from collections.abc import Iterator, Sequence
from pathlib import Path

import numpy as np
import pytest

from paperroute.build.labels import (
    LabelIndex,
    fit_street,
    label_box,
    place_labels,
)
from paperroute.build.map_style import STATION_FACE, STREET_FACE
from paperroute.city import SYDNEY_PROJECTION
from paperroute_device.render.canvas import text_width
from paperroute_transit.build.features import FeatureStore
from paperroute_transit.build.osm_source import OsmPlace
from paperroute_transit.build.tile_store import PlacedLabel
from paperroute_transit.core.geo import Point, to_lonlat
from paperroute_transit.core.tiles import (
    ZOOMS,
    LabelKind,
    from_pixels,
    tiles_covering,
    to_pixels,
)

EDGE = 20480.0  # a block boundary at every zoom
MIDDLE = EDGE + 1024.0
ANCHOR = from_pixels(
    pixel=(MIDDLE, MIDDLE),
    mpp=3,
    projection=SYDNEY_PROJECTION,
)

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
    layer: str = 'road_primary',
) -> None:
    """
    Add a road drawn in a zoom's global pixels.

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
    layer : str
        Road layer.
    """
    store.add_line(
        layer=layer,
        name=name,
        points=np.array([from_pixels(
            pixel=p,
            mpp=mpp,
            projection=SYDNEY_PROJECTION,
        ) for p in pixels]),
    )


def add_place(
    *,
    store: FeatureStore,
    kind: str,
    name: str,
    rank: int = 0,
    point: Point = ANCHOR,
) -> None:
    """
    Add a place, by default at the anchor.

    Parameters
    ----------
    store : FeatureStore
        Target store.
    kind : str
        Place kind, or ``station``.
    name : str
        The name.
    rank : int
        Place rank.
    point : Point
        Position in metres.
    """
    lon, lat = to_lonlat(point=point, projection=SYDNEY_PROJECTION)
    store.add(
        feature=OsmPlace(kind=kind, name=name, rank=rank, lon=lon, lat=lat),
        projection=SYDNEY_PROJECTION,
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
) -> list[PlacedLabel]:
    """
    Place labels around the anchor.

    Parameters
    ----------
    store : FeatureStore
        The features.
    zooms : Sequence[int]
        Zooms to place.

    Returns
    -------
    list[PlacedLabel]
        Every placed label.
    """
    tiles: dict[int, set[tuple[int, int]]] = {}
    for mpp in zooms:
        x, y = to_pixels(point=ANCHOR, mpp=mpp, projection=SYDNEY_PROJECTION)
        tiles[mpp] = around(box=(x - 3072, y - 1024, x + 3072, y + 1024))
    index = LabelIndex(conn=store.conn)
    return list(
        place_labels(
            store=store,
            tiles=tiles,
            index=index,
            projection=SYDNEY_PROJECTION,
        ),
    )


def add_blocker(*, store: FeatureStore) -> None:
    """
    Add a station whose small label sits on the anchor.

    Parameters
    ----------
    store : FeatureStore
        Target store.
    """
    add_place(
        store=store,
        kind='station',
        name='Q',
        point=from_pixels(
            pixel=(MIDDLE, MIDDLE + 15),
            mpp=3,
            projection=SYDNEY_PROJECTION,
        ),
    )


def street_xs(*, labels: Sequence[PlacedLabel]) -> list[float]:
    """
    List the street labels' centre x values.

    Parameters
    ----------
    labels : Sequence[PlacedLabel]
        Placed labels.

    Returns
    -------
    list[float]
        Centre x of each street label, in order.
    """
    return [label.x for label in labels if label.kind == LabelKind.STREET]


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


def test_claim_accepts_a_box_that_only_touches(store: FeatureStore) -> None:
    """Boxes sharing an edge do not overlap."""
    index = LabelIndex(conn=store.conn)
    assert index.claim(box=(0, 0, 10, 10))
    assert index.claim(box=(10, 0, 20, 10))
    assert index.claim(box=(0, 10, 10, 20))


def test_reset_forgets_street_names(store: FeatureStore) -> None:
    """After a reset, a name is no longer near anything."""
    index = LabelIndex(conn=store.conn)
    label = fit_street(
        line=np.array([(0.0, 0.0), (200.0, 0.0)]),
        name='Old Road',
        width=60,
        along=100,
        zoom=3,
    )
    assert label is not None
    index.remember(label=label)
    assert index.near(name='Old Road', x=150, y=0)
    assert not index.near(name='New Road', x=150, y=0)
    index.reset()
    assert not index.near(name='Old Road', x=150, y=0)


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
    assert sorted(label.x for label in labels) == [
        EDGE - 1000 + 192 + 384 * k for k in range(len(labels))
    ]
    width = text_width(text='Long Street', face=STREET_FACE) + 6
    assert {label.width for label in labels} == {width}


def test_suburbs_only_from_8(store: FeatureStore) -> None:
    """A suburb is named from 8 m/px up, in spaced capitals."""
    add_place(store=store, kind='suburb', name='Constitution Hill')
    labels = place(store=store, zooms=ZOOMS)
    assert sorted(label.zoom for label in labels) == [8, 14, 25, 45]
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
    x, y = to_pixels(point=ANCHOR, mpp=8, projection=SYDNEY_PROJECTION)
    assert label.start == pytest.approx((x, y))
    assert (label.x, label.y) == (round(x), round(y - 15))


def test_station_rank_zero_comes_first(store: FeatureStore) -> None:
    """Of two stations at one spot, the full station is named."""
    lon, lat = to_lonlat(point=ANCHOR, projection=SYDNEY_PROJECTION)
    for rank, name in ((1, 'Bus Stop'), (0, 'Rail Station')):
        store.add(
            feature=OsmPlace(
                kind='station',
                name=name,
                rank=rank,
                lon=lon,
                lat=lat,
            ),
            projection=SYDNEY_PROJECTION,
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
        place_labels(
            store=store,
            tiles=tiles,
            index=index,
            projection=SYDNEY_PROJECTION,
        ),
    )
    assert len(labels) >= 4
    assert all(EDGE <= label.x < EDGE + 2048 for label in labels)


def test_label_belongs_to_the_block_holding_its_centre(
    store: FeatureStore,
) -> None:
    """A station just below the block is named; a suburb there is not."""
    for kind, x in (('station', MIDDLE), ('suburb', MIDDLE + 500)):
        below = from_pixels(
            pixel=(x, EDGE + 2048 + 10),
            mpp=8,
            projection=SYDNEY_PROJECTION,
        )
        lon, lat = to_lonlat(point=below, projection=SYDNEY_PROJECTION)
        store.add(
            feature=OsmPlace(kind=kind, name='Edge', rank=0, lon=lon, lat=lat),
            projection=SYDNEY_PROJECTION,
        )
    tiles = {8: around(box=(EDGE, EDGE, EDGE + 2048, EDGE + 2048))}
    index = LabelIndex(conn=store.conn)
    labels = list(
        place_labels(
            store=store,
            tiles=tiles,
            index=index,
            projection=SYDNEY_PROJECTION,
        ),
    )
    assert [(label.kind, label.y) for label in labels] == [
        (LabelKind.STATION, EDGE + 2048 - 5),
    ]


def test_station_width_has_the_margin(store: FeatureStore) -> None:
    """A station label's width is its text plus the margin."""
    add_place(store=store, kind='station', name='Westmead')
    labels = place(store=store, zooms=[8])
    width = text_width(text='Westmead', face=STATION_FACE) + 6
    assert [label.width for label in labels] == [width]


def test_short_street_is_named_at_its_middle(store: FeatureStore) -> None:
    """A street too short for the 192 px step gets one label mid-way."""
    add_street(
        store=store,
        pixels=[(MIDDLE - 75, MIDDLE), (MIDDLE + 75, MIDDLE)],
        mpp=3,
        name='Elm St',
    )
    labels = place(store=store, zooms=[3])
    assert [(label.x, label.y) for label in labels] == [(MIDDLE, MIDDLE)]


def test_250_px_street_gets_one_label(store: FeatureStore) -> None:
    """A 250 px street at 3 m/px is named exactly once."""
    add_street(
        store=store,
        pixels=[(MIDDLE - 125, MIDDLE), (MIDDLE + 125, MIDDLE)],
        mpp=3,
    )
    assert len(place(store=store, zooms=[3])) == 1


def test_short_street_falls_back_to_a_quarter(store: FeatureStore) -> None:
    """When the middle is taken, a quarter point takes the label."""
    add_street(
        store=store,
        pixels=[(MIDDLE - 90, MIDDLE), (MIDDLE + 90, MIDDLE)],
        mpp=3,
        name='Elm St',
    )
    add_blocker(store=store)
    labels = place(store=store, zooms=[3])
    assert street_xs(labels=labels) == [MIDDLE - 45]


def test_offsets_rescue_a_blocked_centre(store: FeatureStore) -> None:
    """A label on the only centre pushes the street name 64 px along."""
    add_street(
        store=store,
        pixels=[(MIDDLE - 192, MIDDLE), (MIDDLE + 108, MIDDLE)],
        mpp=3,
    )
    add_blocker(store=store)
    labels = place(store=store, zooms=[3])
    assert street_xs(labels=labels) == [MIDDLE + 64]


def test_different_streets_close_together_are_both_named(
    store: FeatureStore,
) -> None:
    """The same-name refusal does not stop another street nearby."""
    for name, y in (('A Street', MIDDLE), ('B Street', MIDDLE + 100)):
        add_street(
            store=store,
            pixels=[(MIDDLE - 192, y), (MIDDLE + 108, y)],
            mpp=3,
            name=name,
        )
    labels = place(store=store, zooms=[3])
    assert sorted(label.name for label in labels) == ['A Street', 'B Street']


@pytest.mark.parametrize(
    ('loser', 'winner'),
    [
        (('road_minor', 384), ('road_primary', 384)),
        (('road_primary', 384), ('road_primary', 640)),
    ],
)
def test_more_important_street_wins_a_crossing(
    store: FeatureStore,
    loser: tuple[str, int],
    winner: tuple[str, int],
) -> None:
    """At a crossing, the higher class, then the longer street, wins."""
    layer, length = loser
    add_street(
        store=store,
        pixels=[(MIDDLE - 192, MIDDLE), (MIDDLE - 192 + length, MIDDLE)],
        mpp=3,
        name='Loser Lane',
        layer=layer,
    )
    layer, length = winner
    add_street(
        store=store,
        pixels=[(MIDDLE, MIDDLE - 192), (MIDDLE, MIDDLE - 192 + length)],
        mpp=3,
        name='Winner Way',
        layer=layer,
    )
    labels = place(store=store, zooms=[3])
    at_crossing = [
        label.name
        for label in labels
        if (label.x, label.y) == (MIDDLE, MIDDLE)
    ]
    assert at_crossing == ['Winner Way']


def test_street_class_is_its_highest_part(store: FeatureStore) -> None:
    """A street partly primary ranks as primary, whatever comes last."""
    add_street(
        store=store,
        pixels=[(MIDDLE - 192, MIDDLE), (MIDDLE + 100, MIDDLE)],
        mpp=3,
        name='Mixed Road',
    )
    add_street(
        store=store,
        pixels=[(MIDDLE + 100, MIDDLE), (MIDDLE + 192, MIDDLE)],
        mpp=3,
        name='Mixed Road',
        layer='road_minor',
    )
    add_street(
        store=store,
        pixels=[(MIDDLE, MIDDLE - 192), (MIDDLE, MIDDLE + 192)],
        mpp=3,
        name='Cross Road',
        layer='road_tertiary',
    )
    labels = place(store=store, zooms=[3])
    at_crossing = [
        label.name
        for label in labels
        if (label.x, label.y) == (MIDDLE, MIDDLE)
    ]
    assert at_crossing == ['Mixed Road']


def test_street_pieces_reach_past_the_block(store: FeatureStore) -> None:
    """Ways just outside the block join the piece the block steps along."""
    add_street(
        store=store,
        pixels=[(EDGE - 200, MIDDLE), (EDGE - 10, MIDDLE)],
        mpp=3,
    )
    add_street(
        store=store,
        pixels=[(EDGE - 10, MIDDLE), (EDGE + 600, MIDDLE)],
        mpp=3,
    )
    tiles = {3: around(box=(EDGE, EDGE, EDGE + 2048, EDGE + 2048))}
    index = LabelIndex(conn=store.conn)
    labels = list(
        place_labels(
            store=store,
            tiles=tiles,
            index=index,
            projection=SYDNEY_PROJECTION,
        ),
    )
    assert [label.x for label in labels] == [EDGE + 56, EDGE + 376]


def test_station_keeps_its_label_across_a_block_edge(
    store: FeatureStore,
) -> None:
    """A station just past a block edge beats a street from the block before.

    The street's label centre is in the left block and the station's in
    the right one; their boxes overlap across the edge.
    """
    add_street(
        store=store,
        pixels=[(EDGE - 232, MIDDLE), (EDGE + 68, MIDDLE)],
        mpp=3,
    )
    add_place(
        store=store,
        kind='station',
        name='Westmead',
        point=from_pixels(
            pixel=(EDGE + 20, MIDDLE + 15),
            mpp=3,
            projection=SYDNEY_PROJECTION,
        ),
    )
    labels = place(store=store, zooms=[3])
    assert sorted((label.kind, label.x) for label in labels) == [
        (LabelKind.STATION, EDGE + 20),
        (LabelKind.STREET, EDGE - 104),
    ]


def test_places_go_by_rank(store: FeatureStore) -> None:
    """Of two places at one spot, the lower rank is named."""
    add_place(store=store, kind='suburb', name='Minor', rank=1)
    add_place(store=store, kind='suburb', name='Major', rank=0)
    labels = place(store=store, zooms=[8])
    assert [label.name for label in labels] == ['M A J O R']
