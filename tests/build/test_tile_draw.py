"""Tests for drawing map tiles from the scratch features."""

import math
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Final

import numpy as np
import pytest
from numpy.typing import NDArray

from paperroute.build.features import FeatureStore, load_features
from paperroute.build.labels import TEXT_MARGIN
from paperroute.build.map_style import (
    DARK,
    LIGHT,
    STATION_FACE,
    STREET_FACE,
    SUBURB_FACE,
    SUPERSAMPLE,
)
from paperroute.build.tile_draw import (
    HALO,
    Window,
    big_enough,
    draw_window,
    render_tile,
)
from paperroute.build.tile_store import PlacedLabel
from paperroute.core.geo import MetreBox, to_metres
from paperroute.core.tiles import (
    LABEL_REACH,
    TILE,
    LabelKind,
    from_pixels,
    tile_of,
    to_pixels,
)
from paperroute.render.tile_codec import (
    GREYS,
    diffuse,
    pack_bits,
    quantise4,
    unpack_grey,
)
from paperroute_device.render.canvas import (
    INK,
    PAPER,
    Canvas,
    Face,
    text_width,
)
from paperroute_testkit.build.osm_factory import TINY_BBOX, write_tiny_osm

FRAME_HALF: Final[float] = 800.0  # metres
SEAM_MPP: Final[int] = 3
SEAM_ANGLE: Final[float] = 20.0  # degrees
DIAGONAL_SLACK: Final[int] = 12  # pixels per sloping road crossing a seam
NAME_STYLES: Final[dict[LabelKind, tuple[Face, int]]] = {
    LabelKind.STATION: (STATION_FACE, INK),
    LabelKind.SUBURB: (SUBURB_FACE, DARK),
    LabelKind.STREET: (STREET_FACE, INK),
}


def around(*, lon: float, lat: float, half: float) -> MetreBox:
    """
    Build a metre box centred on a longitude and latitude.

    Parameters
    ----------
    lon : float
        Longitude in degrees.
    lat : float
        Latitude in degrees.
    half : float
        Half the box's side in metres.

    Returns
    -------
    MetreBox
        West, south, east and north in metres.
    """
    x, y = to_metres(lon=lon, lat=lat)
    return (x - half, y - half, x + half, y + half)


def tile_pixel(
    *,
    lon: float,
    lat: float,
    mpp: int,
) -> tuple[Window, tuple[int, int]]:
    """
    Find the tile window holding a point and the point's pixel in it.

    Parameters
    ----------
    lon : float
        Longitude in degrees.
    lat : float
        Latitude in degrees.
    mpp : int
        Metres per pixel.

    Returns
    -------
    tuple[Window, tuple[int, int]]
        The tile's window, and the column and row of the pixel holding
        the point.
    """
    pixel = to_pixels(point=to_metres(lon=lon, lat=lat), mpp=mpp)
    col, row = tile_of(pixel=pixel)
    window = Window(
        mpp=mpp,
        left=col * TILE,
        top=row * TILE,
        width=TILE,
        height=TILE,
    )
    local = (
        math.floor(pixel[0]) - window.left,
        math.floor(pixel[1]) - window.top,
    )
    return window, local


def grey_at(*, store: FeatureStore, lon: float, lat: float, mpp: int) -> int:
    """
    Draw the tile holding a point and read the point's grey.

    Parameters
    ----------
    store : FeatureStore
        The features.
    lon : float
        Longitude in degrees.
    lat : float
        Latitude in degrees.
    mpp : int
        Metres per pixel.

    Returns
    -------
    int
        The grey level at the point.
    """
    window, (x, y) = tile_pixel(lon=lon, lat=lat, mpp=mpp)
    return int(draw_window(store=store, window=window)[y, x])


@pytest.fixture(name='store', scope='module')
def _store(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[FeatureStore]:
    """
    Load the tiny OSM file into a store.

    Parameters
    ----------
    tmp_path_factory : pytest.TempPathFactory
        Pytest's temporary directory factory.

    Yields
    ------
    FeatureStore
        The loaded store.
    """
    folder = tmp_path_factory.mktemp('tile_draw')
    write_tiny_osm(path=folder / 'tiny.osm.pbf')
    store = FeatureStore(path=folder / 'features.db')
    load_features(
        osm=folder / 'tiny.osm.pbf',
        bbox=TINY_BBOX,
        store=store,
        frame=around(lon=151.005, lat=-33.8, half=FRAME_HALF),
        index='flex_mem',
    )
    yield store
    store.conn.close()


@pytest.fixture(name='empty')
def _empty(tmp_path: Path) -> Iterator[FeatureStore]:
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


def seam_windows(*, mpp: int) -> tuple[Window, Window, Window]:
    """
    Give two side-by-side tiles near Constitution Hill and their union.

    Parameters
    ----------
    mpp : int
        Metres per pixel.

    Returns
    -------
    tuple[Window, Window, Window]
        The left tile, the right tile and the 512 by 256 window.
    """
    window, _ = tile_pixel(lon=151.0, lat=-33.8, mpp=mpp)
    right = Window(
        mpp=mpp,
        left=window.left + TILE,
        top=window.top,
        width=TILE,
        height=TILE,
    )
    whole = Window(
        mpp=mpp,
        left=window.left,
        top=window.top,
        width=2 * TILE,
        height=TILE,
    )
    return window, right, whole


def stitched(
    *,
    store: FeatureStore,
    mpp: int,
) -> tuple[NDArray[np.uint8], NDArray[np.uint8]]:
    """
    Draw two neighbouring tiles side by side, and the window they fill.

    Parameters
    ----------
    store : FeatureStore
        The features.
    mpp : int
        Metres per pixel.

    Returns
    -------
    tuple[NDArray[np.uint8], NDArray[np.uint8]]
        The stitched tiles and the whole window.
    """
    left, right, whole = seam_windows(mpp=mpp)
    tiles = np.hstack(
        (
            draw_window(store=store, window=left),
            draw_window(store=store, window=right),
        ),
    )
    return tiles, draw_window(store=store, window=whole)


def edges(*, grey: NDArray[np.uint8]) -> NDArray[np.bool_]:
    """
    Find the pixels whose 3 by 3 neighbourhood is not one level.

    Parameters
    ----------
    grey : NDArray[np.uint8]
        Grey levels.

    Returns
    -------
    NDArray[np.bool_]
        True where a pixel sits on an edge.
    """
    padded = np.pad(grey, 1, mode='edge')
    around = np.lib.stride_tricks.sliding_window_view(padded, (3, 3))
    return np.asarray(around.max(axis=(2, 3)) != around.min(axis=(2, 3)))


def assert_nearly_seamless(
    *,
    tiles: NDArray[np.uint8],
    whole: NDArray[np.uint8],
    slack: int,
) -> None:
    """
    Check stitched tiles match the whole window but for a few edge pixels.

    Smoothed sloping edges are cut at each canvas's border, so a pixel
    whose shade sits on a level boundary can round either way. Each
    such pixel must be one level apart and lie on an edge.

    Parameters
    ----------
    tiles : NDArray[np.uint8]
        The stitched tiles.
    whole : NDArray[np.uint8]
        The whole window.
    slack : int
        Most pixels allowed to differ.
    """
    gap = np.abs(tiles.astype(np.int16) - whole.astype(np.int16))
    assert int(gap.max()) <= GREYS[1]
    assert not (gap.astype(bool) & ~edges(grey=whole)).any()
    assert np.count_nonzero(gap) <= slack


def add_pixel_line(
    *,
    store: FeatureStore,
    layer: str,
    pixels: list[tuple[float, float]],
    mpp: int,
) -> None:
    """
    Add a line drawn in a zoom's global pixels.

    Parameters
    ----------
    store : FeatureStore
        Target store.
    layer : str
        Line layer.
    pixels : list[tuple[float, float]]
        Vertices in global pixels.
    mpp : int
        Zoom the pixels are in.
    """
    store.add_line(
        layer=layer,
        name='',
        points=np.array([from_pixels(pixel=p, mpp=mpp) for p in pixels]),
    )


def tile_window(*, mpp: int) -> Window:
    """
    Give a tile window away from the grid origin.

    Parameters
    ----------
    mpp : int
        Metres per pixel.

    Returns
    -------
    Window
        The tile at column 40, row 30.
    """
    return Window(
        mpp=mpp,
        left=40 * TILE,
        top=30 * TILE,
        width=TILE,
        height=TILE,
    )


def add_pixel_area(
    *,
    store: FeatureStore,
    layer: str,
    box: tuple[float, float, float, float],
    mpp: int,
) -> None:
    """
    Add a rectangular area drawn in a zoom's global pixels.

    Parameters
    ----------
    store : FeatureStore
        Target store.
    layer : str
        Area layer.
    box : tuple[float, float, float, float]
        Left, top, right and bottom in global pixels.
    mpp : int
        Zoom the pixels are in.
    """
    left, top, right, bottom = box
    corners = [
        (left, top),
        (right, top),
        (right, bottom),
        (left, bottom),
        (left, top),
    ]
    store.add_area(
        layer=layer,
        rings=[np.array([from_pixels(pixel=p, mpp=mpp) for p in corners])],
    )


def reference(
    *,
    window: Window,
    labels: list[PlacedLabel],
) -> NDArray[np.uint8]:
    """
    Draw names smoothly at 4x onto a canvas at the window's local pixels.

    Parameters
    ----------
    window : Window
        The window the labels are drawn for.
    labels : list[PlacedLabel]
        Labels in drawing order; station squares are left out.

    Returns
    -------
    NDArray[np.uint8]
        The four-level pixels.
    """
    canvas = Canvas(
        width=window.width,
        height=window.height,
        scale=SUPERSAMPLE,
        smooth=True,
    )
    for label in labels:
        canvas.text(
            at=(label.x - window.left, label.y - window.top),
            text=label.name,
            face=NAME_STYLES[label.kind][0],
            grey=NAME_STYLES[label.kind][1],
            anchor='mm',
            halo=2,
            angle=label.angle,
        )
    return quantise4(grey=canvas.grey())


def rimmed(*, side: int) -> NDArray[np.uint8]:
    """
    Give an ink square inside a one pixel paper rim.

    Parameters
    ----------
    side : int
        The ink square's side in pixels.

    Returns
    -------
    NDArray[np.uint8]
        A square ``side + 2`` pixels across.
    """
    ink = np.full((side, side), INK, dtype=np.uint8)
    return np.pad(ink, 1, constant_values=PAPER)


def test_tile_has_four_levels_only(store: FeatureStore) -> None:
    window, _ = tile_pixel(lon=151.0, lat=-33.802, mpp=3)
    tile = draw_window(store=store, window=window)
    assert tile.shape == (TILE, TILE)
    assert tile.dtype == np.uint8
    assert set(np.unique(tile).tolist()) <= set(GREYS)


def test_primary_road_is_dark_at_3(store: FeatureStore) -> None:
    assert grey_at(store=store, lon=151.003, lat=-33.802, mpp=3) == DARK


def test_minor_road_is_light_at_14(store: FeatureStore) -> None:
    assert grey_at(store=store, lon=151.003, lat=-33.8, mpp=14) == LIGHT


def test_park_is_light_and_sea_is_dark(store: FeatureStore) -> None:
    assert grey_at(store=store, lon=150.997, lat=-33.795, mpp=3) == LIGHT
    assert grey_at(store=store, lon=151.011, lat=-33.8, mpp=3) == DARK


def street_label(
    *,
    name: str,
    centre: tuple[float, float],
    angle: float,
) -> PlacedLabel:
    """
    Build a street label along a straight stretch.

    Parameters
    ----------
    name : str
        The street name.
    centre : tuple[float, float]
        Centre in global pixels.
    angle : float
        Rotation in degrees.

    Returns
    -------
    PlacedLabel
        The label, as wide as its text plus the margin.
    """
    width = text_width(text=name, face=STREET_FACE) + TEXT_MARGIN
    turn = math.radians(angle)
    reach = (math.cos(turn) * width / 2, math.sin(turn) * width / 2)
    return PlacedLabel(
        name=name,
        x=centre[0],
        y=centre[1],
        angle=angle,
        width=width,
        start=(centre[0] - reach[0], centre[1] - reach[1]),
        end=(centre[0] + reach[0], centre[1] + reach[1]),
        zoom=SEAM_MPP,
        kind=LabelKind.STREET,
    )


def test_label_crossing_a_tile_edge_is_seamless(
    empty: FeatureStore,
) -> None:
    left, _, _ = seam_windows(mpp=SEAM_MPP)
    edge = left.left + TILE
    label = street_label(
        name='Seam Street',
        centre=(float(edge), float(left.top + TILE // 2)),
        angle=SEAM_ANGLE,
    )
    add_pixel_line(
        store=empty,
        layer='road_primary',
        pixels=[label.start, label.end],
        mpp=SEAM_MPP,
    )
    empty.add_labels(labels=[label])
    tiles, whole = stitched(store=empty, mpp=SEAM_MPP)
    assert (whole[:, TILE - TILE // 4 : TILE + TILE // 4] == INK).any()
    assert_nearly_seamless(tiles=tiles, whole=whole, slack=1)


def test_turned_label_crossing_a_tile_edge_is_exact(
    empty: FeatureStore,
) -> None:
    left, _, _ = seam_windows(mpp=SEAM_MPP)
    label = street_label(
        name='Seam Street',
        centre=(float(left.left + TILE), float(left.top + TILE // 2)),
        angle=SEAM_ANGLE,
    )
    empty.add_labels(labels=[label])
    tiles, whole = stitched(store=empty, mpp=SEAM_MPP)
    assert (whole[:, TILE - 8 : TILE + 8] == INK).any()
    np.testing.assert_array_equal(tiles, whole)


def test_sloping_road_crossing_a_tile_edge_is_nearly_seamless(
    empty: FeatureStore,
) -> None:
    left, _, _ = seam_windows(mpp=SEAM_MPP)
    x, y = float(left.left + TILE), float(left.top + TILE // 2)
    add_pixel_line(
        store=empty,
        layer='road_primary',
        pixels=[(x - 150, y - 55), (x + 150, y + 55)],
        mpp=SEAM_MPP,
    )
    tiles, whole = stitched(store=empty, mpp=SEAM_MPP)
    assert (whole[:, TILE - 2 : TILE + 2] == DARK).any()
    assert_nearly_seamless(tiles=tiles, whole=whole, slack=DIAGONAL_SLACK)


def test_seven_sloping_roads_crossing_a_tile_edge_are_nearly_seamless(
    empty: FeatureStore,
) -> None:
    left, _, _ = seam_windows(mpp=SEAM_MPP)
    x, y = float(left.left + TILE), float(left.top)
    roads = (
        ('road_minor', 5.0),
        ('road_tertiary', 17.0),
        ('road_secondary', 29.0),
        ('road_primary', 41.0),
        ('road_trunk', 57.0),
        ('road_motorway', 69.0),
        ('river', 81.0),
    )
    for step, (layer, angle) in enumerate(roads):
        turn = math.radians(angle)
        reach = (150 * math.cos(turn), 150 * math.sin(turn))
        middle = y + 20.3 + 32 * step
        add_pixel_line(
            store=empty,
            layer=layer,
            pixels=[
                (x - reach[0], middle - reach[1]),
                (x + reach[0], middle + reach[1]),
            ],
            mpp=SEAM_MPP,
        )
    tiles, whole = stitched(store=empty, mpp=SEAM_MPP)
    assert (whole[:, TILE - 2 : TILE + 2] == DARK).sum() >= len(roads)
    assert_nearly_seamless(
        tiles=tiles,
        whole=whole,
        slack=len(roads) * DIAGONAL_SLACK,
    )


def test_roads_beside_a_tile_edge_are_seamless(
    empty: FeatureStore,
) -> None:
    left, _, _ = seam_windows(mpp=SEAM_MPP)
    x, y = float(left.left + TILE), float(left.top)
    lines = (
        ('road_motorway', [(x + 2.5, y - 20), (x + 2.5, y + 120)]),
        ('road_motorway', [(x - 2.5, y + 140), (x - 2.5, y + 280)]),
        ('river', [(x - 100, y + 64.5), (x + 100, y + 64.5)]),
    )
    for layer, pixels in lines:
        add_pixel_line(store=empty, layer=layer, pixels=pixels, mpp=SEAM_MPP)
    tiles, whole = stitched(store=empty, mpp=SEAM_MPP)
    assert (whole[:, TILE - 2] == DARK).any()
    assert (whole[:, TILE + 1] == DARK).any()
    np.testing.assert_array_equal(tiles, whole)


def test_long_label_reaches_the_next_tile(empty: FeatureStore) -> None:
    left, _, _ = seam_windows(mpp=SEAM_MPP)
    label = street_label(
        name='Great Western Highway Northern Service Road East',
        centre=(float(left.left + TILE - 80), float(left.top + 100)),
        angle=0.0,
    )
    empty.add_labels(labels=[label])
    tiles, whole = stitched(store=empty, mpp=SEAM_MPP)
    assert label.width / 2 > 80 + 40
    assert (whole[:, TILE + 40 :] == INK).any()
    np.testing.assert_array_equal(tiles, whole)


def test_label_too_wide_for_the_reach_is_not_drawn(
    empty: FeatureStore,
) -> None:
    window = Window(mpp=SEAM_MPP, left=0, top=0, width=TILE, height=TILE)
    wide = street_label(name='Wide ' * 60, centre=(128.0, 64.0), angle=0.0)
    fits = street_label(name='Fits ' * 18, centre=(128.0, 192.0), angle=0.0)
    empty.add_labels(labels=[wide, fits])
    tile = draw_window(store=empty, window=window)
    assert wide.width / 2 + 2 > LABEL_REACH
    assert fits.width / 2 + 2 < LABEL_REACH
    assert (tile[: TILE // 2] == PAPER).all()
    assert (tile[TILE // 2 :] == INK).any()


def test_crossing_transit_is_seamless(empty: FeatureStore) -> None:
    left, _, _ = seam_windows(mpp=SEAM_MPP)
    x, y = float(left.left), float(left.top)
    lines = (
        ('rail', [(x + 10, y + 50.5), (x + 90, y + 50.5)]),
        ('light_rail', [(x + 276.5, y - 10), (x + 276.5, y + 260)]),
        ('rail', [(x + 200, y + 128.5), (x + 346, y + 128.5)]),
    )
    for layer, pixels in lines:
        add_pixel_line(store=empty, layer=layer, pixels=pixels, mpp=SEAM_MPP)
    tiles, whole = stitched(store=empty, mpp=SEAM_MPP)
    np.testing.assert_array_equal(tiles, whole)


def test_one_pixel_roads_land_on_exact_pixels(empty: FeatureStore) -> None:
    window = tile_window(mpp=14)
    x, y = float(window.left), float(window.top)
    for pixels in (
        [(x - 20, y + 100.5), (x + 300, y + 100.5)],
        [(x + 60.5, y - 20), (x + 60.5, y + 300)],
    ):
        add_pixel_line(
            store=empty,
            layer='road_minor',
            pixels=pixels,
            mpp=14,
        )
    tile = draw_window(store=empty, window=window)
    assert (tile[100] == LIGHT).all()
    assert (tile[:, 60] == LIGHT).all()
    assert (tile[[99, 101], :60] == PAPER).all()
    assert (tile[:100, [59, 61]] == PAPER).all()


def test_road_between_pixels_shades_its_edges(empty: FeatureStore) -> None:
    window = tile_window(mpp=14)
    x, y = float(window.left), float(window.top)
    add_pixel_line(
        store=empty,
        layer='road_primary',
        pixels=[(x - 20, y + 100.25), (x + 300, y + 100.25)],
        mpp=14,
    )
    tile = draw_window(store=empty, window=window)
    profile = [PAPER, LIGHT, DARK, DARK, LIGHT, PAPER]
    assert (tile[97:103] == np.array(profile)[:, None]).all()


def test_diagonal_road_edges_are_smoothed(empty: FeatureStore) -> None:
    window = tile_window(mpp=SEAM_MPP)
    x, y = float(window.left), float(window.top)
    add_pixel_line(
        store=empty,
        layer='road_primary',
        pixels=[(x + 20, y + 20), (x + 220, y + 120)],
        mpp=SEAM_MPP,
    )
    tile = draw_window(store=empty, window=window)
    along = [
        tile[20 + math.floor((step + 0.5) / 2), 20 + step]
        for step in range(200)
    ]
    assert set(along) == {DARK}
    assert (tile == LIGHT).any()


def test_smoothed_names_stay_inside_their_width(
    empty: FeatureStore,
) -> None:
    window = tile_window(mpp=SEAM_MPP)
    x, y = float(window.left), float(window.top)
    labels = [
        street_label(name=name, centre=(x + 128, y + row), angle=0.0)
        for name, row in (
            ('Church Street', 30),
            ('Jaguar Way', 70),
            ('jjj', 110),
            ('Fits ' * 18, 150),
            ('Great Western Highway Service Road', 200),
        )
    ]
    add_pixel_area(
        store=empty,
        layer='water',
        box=(x - 10, y - 10, x + 300, y + 300),
        mpp=SEAM_MPP,
    )
    empty.add_labels(labels=labels)
    tile = draw_window(store=empty, window=window)
    for label in labels:
        middle = round(label.y) - window.top
        rows = slice(middle - 10, middle + 10)
        drawn = np.nonzero((tile[rows] != DARK).any(axis=0))[0]
        half = label.width / 2 + HALO
        assert drawn.min() >= 128 - half
        assert drawn.max() + 1 <= 128 + half


def point_label(
    *,
    name: str,
    kind: LabelKind,
    point: tuple[float, float],
    rank: int,
) -> PlacedLabel:
    """
    Build a level station or suburb label.

    Parameters
    ----------
    name : str
        The name.
    kind : LabelKind
        Station or suburb.
    point : tuple[float, float]
        The feature's point in global pixels.
    rank : int
        Place rank.

    Returns
    -------
    PlacedLabel
        The label, 15 px above a station's point and on a suburb's.
    """
    rise = 15 if kind == LabelKind.STATION else 0
    return PlacedLabel(
        name=name,
        x=float(round(point[0])),
        y=float(round(point[1] - rise)),
        angle=0.0,
        width=60.0,
        start=point,
        end=point,
        zoom=SEAM_MPP,
        kind=kind,
        rank=rank,
    )


def test_points_crossing_a_tile_edge_are_seamless(
    empty: FeatureStore,
) -> None:
    left, _, _ = seam_windows(mpp=SEAM_MPP)
    edge = left.left + TILE
    empty.add_labels(
        labels=[
            point_label(
                name='Central',
                kind=LabelKind.STATION,
                point=(edge + 0.3, left.top + 60.6),
                rank=0,
            ),
            point_label(
                name='Halt',
                kind=LabelKind.STATION,
                point=(edge - 2.4, left.top + 140.2),
                rank=1,
            ),
            point_label(
                name='C O N S T I T U T I O N',
                kind=LabelKind.SUBURB,
                point=(edge + 1.0, left.top + 200.0),
                rank=0,
            ),
        ],
    )
    tiles, whole = stitched(store=empty, mpp=SEAM_MPP)
    assert (whole == INK).any()
    assert (whole == DARK).any()
    np.testing.assert_array_equal(tiles, whole)


def test_station_squares_land_on_exact_pixels(empty: FeatureStore) -> None:
    window = tile_window(mpp=SEAM_MPP)
    x, y = float(window.left), float(window.top)
    add_pixel_area(
        store=empty,
        layer='park',
        box=(x - 10, y - 10, x + 300, y + 300),
        mpp=SEAM_MPP,
    )
    empty.add_labels(
        labels=[
            point_label(
                name='A',
                kind=LabelKind.STATION,
                point=(x + 40, y + 140),
                rank=0,
            ),
            point_label(
                name='B',
                kind=LabelKind.STATION,
                point=(x + 200, y + 140),
                rank=1,
            ),
        ],
    )
    tile = draw_window(store=empty, window=window)
    assert (tile[134:146, 34:46] == rimmed(side=10)).all()
    assert (tile[136:144, 196:204] == rimmed(side=6)).all()
    assert (tile[[133, 146], 34:46] == LIGHT).all()
    assert (tile[135, 196:204] == LIGHT).all()


def test_names_land_where_the_canvas_puts_them(empty: FeatureStore) -> None:
    window = tile_window(mpp=SEAM_MPP)
    x, y = float(window.left), float(window.top)
    station = point_label(
        name='Central',
        kind=LabelKind.STATION,
        point=(x + 130, y + 112),
        rank=0,
    )
    labels = [
        replace(station, start=(x - TILE, y - TILE)),
        point_label(
            name='C O N S T I T U T I O N',
            kind=LabelKind.SUBURB,
            point=(x + 120, y + 100),
            rank=0,
        ),
        street_label(
            name='Crossing Street',
            centre=(x + 128, y + 104),
            angle=SEAM_ANGLE,
        ),
    ]
    empty.add_labels(labels=list(reversed(labels)))
    tile = draw_window(store=empty, window=window)
    np.testing.assert_array_equal(
        tile,
        reference(window=window, labels=labels),
    )
    assert not np.array_equal(
        tile,
        reference(window=window, labels=labels[::-1]),
    )


def test_halo_clears_paper_around_names(empty: FeatureStore) -> None:
    window = tile_window(mpp=SEAM_MPP)
    x, y = float(window.left), float(window.top)
    add_pixel_area(
        store=empty,
        layer='park',
        box=(x - 10, y - 10, x + 300, y + 300),
        mpp=SEAM_MPP,
    )
    empty.add_labels(
        labels=[
            street_label(
                name='Halo Street',
                centre=(x + 128, y + 128),
                angle=0.0,
            ),
        ],
    )
    tile = draw_window(store=empty, window=window)
    assert (tile == INK).any()
    assert (tile == PAPER).any()


def test_street_names_baked_in_ink(empty: FeatureStore) -> None:
    window = tile_window(mpp=SEAM_MPP)
    x, y = float(window.left), float(window.top)
    label = street_label(
        name='Ink Street',
        centre=(x + 128, y + 128),
        angle=0.0,
    )
    empty.add_labels(labels=[label])
    tile = draw_window(store=empty, window=window)
    assert (tile[120:137, 80:177] == INK).any()
    np.testing.assert_array_equal(
        tile,
        reference(window=window, labels=[label]),
    )


def test_suburb_names_stay_dark(empty: FeatureStore) -> None:
    window = tile_window(mpp=SEAM_MPP)
    x, y = float(window.left), float(window.top)
    empty.add_labels(
        labels=[
            point_label(
                name='DARKTON',
                kind=LabelKind.SUBURB,
                point=(x + 128, y + 128),
                rank=0,
            ),
        ],
    )
    tile = draw_window(store=empty, window=window)
    assert tile.min() == DARK


def test_water_is_drawn_over_parks(empty: FeatureStore) -> None:
    window = tile_window(mpp=SEAM_MPP)
    x, y = float(window.left), float(window.top)
    for layer, box in (
        ('water', (x + 50, y + 50, x + 150, y + 150)),
        ('park', (x + 100, y + 100, x + 200, y + 200)),
    ):
        add_pixel_area(store=empty, layer=layer, box=box, mpp=SEAM_MPP)
    tile = draw_window(store=empty, window=window)
    assert tile[75, 75] == DARK
    assert tile[125, 125] == DARK
    assert tile[175, 175] == LIGHT


@pytest.mark.parametrize(
    ('side', 'expected'),
    [(3.0, False), (3.1, True)],
)
def test_big_enough_needs_more_than_three_pixels_squared(
    side: float,
    expected: bool,
) -> None:
    ring = np.array([(0, 0), (side * 8, 0), (side * 8, 24), (0, 24), (0, 0)])
    assert big_enough(rings=[ring], mpp=8) is expected


def test_small_area_is_not_filled(empty: FeatureStore) -> None:
    window = tile_window(mpp=SEAM_MPP)
    x, y = float(window.left), float(window.top)
    add_pixel_area(
        store=empty,
        layer='park',
        box=(x + 10, y + 10, x + 12.9, y + 13),
        mpp=SEAM_MPP,
    )
    assert (draw_window(store=empty, window=window) == PAPER).all()


def test_dashed_rail_crossing_a_tile_edge_is_seamless(
    empty: FeatureStore,
) -> None:
    left, _, _ = seam_windows(mpp=SEAM_MPP)
    middle = left.top + TILE // 2 + 0.5
    add_pixel_line(
        store=empty,
        layer='rail',
        pixels=[(left.left - 100.0, middle), (left.left + 600.0, middle)],
        mpp=SEAM_MPP,
    )
    tiles, whole = stitched(store=empty, mpp=SEAM_MPP)
    line = whole[int(middle) - left.top]
    assert (line == INK).any()
    assert (line == PAPER).any()
    np.testing.assert_array_equal(tiles, whole)


def test_render_tile_round_trips(store: FeatureStore) -> None:
    window, _ = tile_pixel(lon=151.0, lat=-33.802, mpp=3)
    grey, bits = render_tile(
        store=store,
        mpp=3,
        col=window.left // TILE,
        row=window.top // TILE,
    )
    np.testing.assert_array_equal(
        unpack_grey(blob=grey),
        draw_window(store=store, window=window),
    )
    assert bits == pack_bits(
        paper=diffuse(grey=draw_window(store=store, window=window)),
    )


def test_empty_window_is_paper(empty: FeatureStore) -> None:
    window = Window(mpp=8, left=0, top=0, width=TILE, height=TILE)
    tile = draw_window(store=empty, window=window)
    assert (tile == PAPER).all()
