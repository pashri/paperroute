"""Shared fixtures for the tile draw tests."""

from collections.abc import Iterator
from pathlib import Path

import pytest

from paperroute_sydney.city import SYDNEY_PROJECTION
from paperroute_testkit.build.osm_factory import TINY_BBOX, write_tiny_osm
from paperroute_transit.build.features import FeatureStore, load_features
from tests.build.tile_draw.helpers import FRAME_HALF, around


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
        projection=SYDNEY_PROJECTION,
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
