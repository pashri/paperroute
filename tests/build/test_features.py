"""Tests for the scratch map feature store."""

from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pytest

from paperroute.build.features import (
    FeatureStore,
    load_features,
    pack_rings,
    unpack_rings,
)
from paperroute.build.osm_source import OsmArea, OsmLine, OsmPlace
from paperroute.build.tile_store import PlacedLabel
from paperroute.core.geo import MetreBox, to_metres
from paperroute.core.tiles import LabelKind
from tests.build.osm_factory import TINY_BBOX, write_tiny_osm


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
    folder = tmp_path_factory.mktemp('features')
    write_tiny_osm(path=folder / 'tiny.osm.pbf')
    store = FeatureStore(path=folder / 'features.db')
    count = load_features(
        osm=folder / 'tiny.osm.pbf',
        bbox=TINY_BBOX,
        store=store,
        frame=around(lon=151.005, lat=-33.8, half=800.0),
        index='flex_mem',
    )
    assert count == 7
    yield store
    store.conn.close()


def test_store_projects_and_indexes(store: FeatureStore) -> None:
    """A box around Hollis Street finds it; a far box finds nothing."""
    near = store.lines_in(box=around(lon=151.0, lat=-33.8, half=20.0))
    far = store.lines_in(box=around(lon=151.2, lat=-33.8, half=20.0))
    assert [(layer, name) for layer, name, _ in near] == [
        ('road_minor', 'Hollis Street'),
    ]
    assert near[0][2][0].tolist() == pytest.approx(
        list(to_metres(lon=150.995, lat=-33.8)),
    )
    assert not far


def test_store_keeps_coastline_out_of_lines(store: FeatureStore) -> None:
    """Coastline makes sea areas and is never stored as a line."""
    everything = around(lon=151.0, lat=-33.8, half=5000.0)
    layers = {layer for layer, _, _ in store.lines_in(box=everything)}
    areas = {layer for layer, _ in store.areas_in(box=everything)}
    assert layers == {'road_minor', 'road_primary', 'rail'}
    assert areas == {'park', 'sea'}


def test_store_places_in_box(store: FeatureStore) -> None:
    """Places come back projected with their kind and rank."""
    places = store.places_in(box=around(lon=151.0, lat=-33.8, half=1000.0))
    assert sorted(place[:3] for place in places) == [
        ('station', 'Parramatta', 0),
        ('suburb', 'Constitution Hill', 0),
    ]


def test_sea_lies_east_of_the_coast(store: FeatureStore) -> None:
    """The sea areas sit east of the coastline at 151.008."""
    coast_x = to_metres(lon=151.008, lat=-33.8)[0]
    sea = [
        ring
        for layer, rings in store.areas_in(
            box=around(lon=151.0, lat=-33.8, half=5000.0),
        )
        if layer == 'sea'
        for ring in rings
    ]
    assert sea
    assert min(ring[:, 0].min() for ring in sea) == pytest.approx(coast_x)


def test_readonly_store_reads_the_same(store: FeatureStore) -> None:
    """A read-only store on the same file sees the committed lines."""
    path = Path(store.conn.execute('PRAGMA database_list').fetchone()[2])
    reader = FeatureStore(path=path, readonly=True)
    box = around(lon=151.0, lat=-33.8, half=20.0)
    assert len(reader.lines_in(box=box)) == len(store.lines_in(box=box))
    reader.conn.close()


def test_labels_round_trip(tmp_path: Path) -> None:
    """Labels come back when their centre lies in the pixel box."""
    store = FeatureStore(path=tmp_path / 'features.db')
    label = PlacedLabel(
        name='Church St',
        x=100.0,
        y=200.0,
        angle=12.5,
        width=40.0,
        start=(80.0, 196.0),
        end=(120.0, 204.0),
        zoom=3,
        kind=LabelKind.STREET,
        rank=0,
    )
    store.add_labels(labels=[label])
    assert store.labels_in(zoom=3, box=(90.0, 190.0, 110.0, 210.0)) == [
        label,
    ]
    assert not store.labels_in(zoom=5, box=(90.0, 190.0, 110.0, 210.0))
    assert not store.labels_in(zoom=3, box=(0.0, 0.0, 50.0, 50.0))
    store.conn.close()


def test_add_projects_areas_and_places(tmp_path: Path) -> None:
    """Added areas and places are stored in metres."""
    store = FeatureStore(path=tmp_path / 'features.db')
    ring = np.array([[151.0, -33.8], [151.001, -33.8], [151.0, -33.801]])
    store.add(feature=OsmArea(layer='water', rings=(ring,)))
    store.add(
        feature=OsmPlace(kind='town', name='Town', rank=0, lon=151, lat=-33),
    )
    store.add(feature=OsmLine(layer='ferry', name='', lonlat=ring))
    store.finish(frame=around(lon=151.0, lat=-33.8, half=100.0))
    ((layer, (stored,)),) = store.areas_in(
        box=around(lon=151.0, lat=-33.8, half=10.0),
    )
    assert layer == 'water'
    assert stored[1].tolist() == pytest.approx(
        list(to_metres(lon=151.001, lat=-33.8)),
    )
    ((_, _, _, point),) = store.places_in(
        box=around(lon=151.0, lat=-33.0, half=10.0),
    )
    assert point == pytest.approx(to_metres(lon=151.0, lat=-33.0))
    store.conn.close()


def test_pack_rings_round_trips() -> None:
    """Rings of different lengths survive packing."""
    rings = [
        np.array([[0.5, 1.5], [2.0, 3.0]]),
        np.array([[4.0, 5.0], [6.0, 7.0], [8.25, 9.0]]),
    ]
    unpacked = unpack_rings(blob=pack_rings(rings=rings))
    assert [ring.tolist() for ring in unpacked] == [
        ring.tolist() for ring in rings
    ]
