"""Reaches read from Sydney's real timetable, when it is there."""

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Final

from paperroute_device_testkit.paths import ROOT
from paperroute_sydney.city import SYDNEY, SydneyMode
from paperroute_transit.adapters.network_db import SqliteNetworkRepository
from paperroute_transit.core.model import StopPlace
from paperroute_transit.core.reach import Reach, StripKey, build_reach

REAL_DB: Final[Path] = ROOT / 'data' / 'timetable.db'
TOWN_HALL: Final[str] = '200070'
STRATHFIELD: Final[str] = '213510'
BLACKTOWN: Final[str] = '214810'


def has_runs(*, path: Path) -> bool:
    """
    Say whether a timetable file exists and holds the run tables.

    Parameters
    ----------
    path : Path
        Timetable file.

    Returns
    -------
    bool
        True when the runs table is there.
    """
    if not path.exists():
        return False
    with closing(sqlite3.connect(f'file:{path}?mode=ro', uri=True)) as conn:
        found = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'runs'",
        ).fetchall()
    return bool(found)


def train_reach(*, station: str) -> tuple[Reach, set[str]]:
    """
    Build a station's Train strip reach from the real timetable.

    Parameters
    ----------
    station : str
        Station ID.

    Returns
    -------
    tuple[Reach, set[str]]
        The reach, and every station after the From in its tails.
    """
    with closing(SqliteNetworkRepository(
        path=REAL_DB,
        city=SYDNEY,
    )) as repo:
        found = repo.reach_input(
            key=StripKey(station=station, kind=SydneyMode.TRAIN, routes=()),
            origin=StopPlace(stop_id=station, name=station, point=None),
        )
    assert found is not None
    after = {key for each in found.tails for key in each.after}
    return build_reach(data=found), after
