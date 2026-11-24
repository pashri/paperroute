"""Tests for timetable db: old schema."""

import os
import sqlite3
from contextlib import closing
from pathlib import Path

from paperroute_sydney.city import SYDNEY_PROJECTION, SydneyFeed
from paperroute_transit.adapters.timetable_db import SqliteTimetableRepository
from paperroute_transit.core.geo import to_metres
from tests.adapters.timetable_db.helpers import (
    AROUND,
    HOLLIS,
    bundle_db,
    bus_bundle_db,
    hollis_departures,
    opened,
)


def test_modes_near_finds_bus_at_the_606_stop(
    slice_repo: SqliteTimetableRepository,
) -> None:
    """A box around the 606 stop holds buses; one offshore holds nothing."""
    x, y = slice_repo.stop(stop_id=HOLLIS).point or (0.0, 0.0)
    box = (x - 2000.0, y - 2000.0, x + 2000.0, y + 2000.0)
    assert SydneyFeed.BUS in slice_repo.modes_near(box=box)
    sx, sy = to_metres(lon=155.0, lat=-33.0, projection=SYDNEY_PROJECTION)
    far = (sx - 2000.0, sy - 2000.0, sx + 2000.0, sy + 2000.0)
    assert slice_repo.modes_near(box=far) == frozenset()


def test_old_schema_timetable_still_answers(tmp_path: Path) -> None:
    """A timetable without shapes answers departures and empty maps."""
    db = bundle_db(tmp_path=tmp_path, days=3)
    with closing(sqlite3.connect(db)) as conn:
        conn.execute('ALTER TABLE trips DROP COLUMN shape_id')
        conn.execute('DROP TABLE shapes')
        conn.commit()
    with opened(path=db) as repo:
        found = hollis_departures(repo=repo)
        assert [d.shape_id for d in found] == [None]
        assert repo.shapes(shape_ids=['bundle:x']) == {}
        assert repo.modes_near(box=(-1e6, -1e6, 1e6, 1e6)) == frozenset()
        pattern = repo.patterns(trip_ids=['bus-0803'], around=AROUND)
        assert pattern['bus-0803'].shape_id is None


def test_old_schema_checked_after_reopen(tmp_path: Path) -> None:
    """Shapes are read from a replacement file with the new schema."""
    old = bus_bundle_db(tmp_path=tmp_path, folder='old')
    with closing(sqlite3.connect(old)) as conn:
        conn.execute('ALTER TABLE trips DROP COLUMN shape_id')
        conn.execute('DROP TABLE shapes')
        conn.commit()
    new = bus_bundle_db(tmp_path=tmp_path, folder='new', shaped=True)
    with opened(path=old) as repo:
        assert repo.shapes(shape_ids=['bundle:S1']) == {}
        os.replace(new, old)
        assert list(repo.shapes(shape_ids=['bundle:S1'])) == ['bundle:S1']
