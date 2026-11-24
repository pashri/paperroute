"""Tests for rail build: chooser."""

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from paperroute_cambridge.city import CAMBRIDGE
from paperroute_transit.adapters.network_db import SqliteNetworkRepository
from paperroute_transit.core.chooser import (
    ChooserRequest,
    ChooserState,
    ColumnRequest,
    Level,
    PreviewsRequest,
    opened,
)
from paperroute_transit.core.chooser_lists import (
    pressed,
    turned,
    with_column,
    with_previews,
)
from paperroute_transit.core.chooser_previews import read_previews
from paperroute_transit.core.chooser_station import knob_pressed
from paperroute_transit.core.model import Pin, StopPlace
from tests.build.rail_build.helpers import built
from tests.build.test_rail_cut import changed, with_norwich
from tests.conftest import RECORDED_DAY


@pytest.fixture(name='norwich_built', scope='module')
def norwich_built_fixture(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """
    Build the recorded day with trains, adding a train to Norwich.

    Parameters
    ----------
    tmp_path_factory : pytest.TempPathFactory
        Makes the directory for the files.

    Returns
    -------
    Path
        The timetable file.
    """
    out = tmp_path_factory.mktemp('norwich')
    rail = changed(out=out / 'norwich.zip', change=with_norwich)
    return built(
        out=out / 'timetable.db', start=RECORDED_DAY, days=1, rail=rail,
    )


def test_the_train_chooser_lists_lines_by_where_they_go(
    norwich_built: Path,
) -> None:
    """Train lists drawing B's lines, ruled off between operators."""
    line = ((Level.KIND, 'train'), (Level.LINE, 'train:GA:Norwich'))
    gn = (
        (Level.KIND, 'train'),
        (Level.LINE, 'train:GN:Kings Lynn & Kings Cross'),
    )
    with closing(
        SqliteNetworkRepository(city=CAMBRIDGE, path=norwich_built),
    ) as network:
        kinds = network.kind_column()
        lines = network.line_column(kind='train')
        stations = network.next_column(path=line)
        towards = network.next_column(
            path=(*gn, (Level.STATION, 'rail:CBG')),
        )
    assert [choice.label for choice in kinds.items] == [
        'Bus', 'Coach', 'Train',
    ]
    assert lines is not None and stations is not None and towards is not None
    assert 'rail:KGX' in [choice.key for choice in towards.items]
    assert lines.items[3].value == 'GA:Norwich'
    assert [(c.key, c.badge, c.label) for c in lines.items] == [
        ('train:GN:Kings Lynn & Kings Cross', 'GN',
         'Kings Lynn & Kings Cross'),
        ('train:TL:Brighton via St Pancras', 'TL', 'Brighton via St Pancras'),
        ('train:GA:Liverpool Street', 'GA', 'Liverpool Street'),
        ('train:GA:Norwich', 'GA', 'Norwich'),
        ('train:XC:Birmingham & Stansted', 'XC', 'Birmingham & Stansted'),
        ('train:EM:Norwich & Liverpool', 'EM', 'Norwich & Liverpool'),
    ]
    assert [c.rule_above for c in lines.items] == [
        False, True, True, False, True, True,
    ]
    assert {'rail:CBG', 'rail:ELY', 'rail:NRW'} <= {
        c.key for c in stations.items
    }


def fed_from(
    *,
    chooser: ChooserState,
    request: ChooserRequest | Pin | None,
    network: SqliteNetworkRepository,
) -> ChooserState:
    """
    Answer column and previews requests from the repository.

    Parameters
    ----------
    chooser : ChooserState
        Chooser waiting for a column or previews.
    request : ChooserRequest | Pin | None
        Its request.
    network : SqliteNetworkRepository
        Where the answers come from.

    Returns
    -------
    ChooserState
        The chooser with the answers, once it asks for nothing more.
    """
    while isinstance(request, ColumnRequest | PreviewsRequest):
        if isinstance(request, ColumnRequest):
            chooser, request = with_column(
                chooser=chooser,
                path=request.path,
                column=network.choices(path=request.path),
            )
        else:
            previews = read_previews(
                network=network, path=request.path, steps=request.steps,
            )
            chooser = with_previews(
                chooser=chooser, path=request.path, previews=previews,
            )
            request = None
    return chooser


def turned_to_key(*, chooser: ChooserState, key: str) -> ChooserState:
    """
    Turn the focused column's cursor onto a key.

    Parameters
    ----------
    chooser : ChooserState
        Chooser in the LISTS stage.
    key : str
        The row's key.

    Returns
    -------
    ChooserState
        The chooser with the cursor on the row.
    """
    keys = [c.key for c in chooser.columns[chooser.focus].items]
    steps = keys.index(key) - chooser.cursors[chooser.focus]
    return turned(chooser=chooser, steps=steps, reach=None)[0]


def test_the_train_chooser_walks_kind_station_to(
    norwich_built: Path,
) -> None:
    """Cambridge's train opens stations, then the To; no line step."""
    with closing(
        SqliteNetworkRepository(city=CAMBRIDGE, path=norwich_built),
    ) as network:
        chooser, request = opened(
            shown=Pin(origin=StopPlace(stop_id='x', name='x', point=None)),
            modes=CAMBRIDGE.modes,
        )
        chooser = fed_from(chooser=chooser, request=request, network=network)
        chooser = turned_to_key(chooser=chooser, key='train')
        chooser, _ = pressed(chooser=chooser, reach=None)
        chooser = turned_to_key(chooser=chooser, key='rail:CBG')
        chooser, asked = knob_pressed(chooser=chooser, reach=None)
        chooser = fed_from(chooser=chooser, request=asked, network=network)
        chooser = turned_to_key(chooser=chooser, key='rail:ELY')
        _, pin = knob_pressed(chooser=chooser, reach=None)
    assert [column.level for column in chooser.columns] == [
        Level.KIND, Level.STATION, Level.TO,
    ]
    assert isinstance(pin, Pin)
    assert pin.origin.stop_id == 'rail:CBG'
    assert pin.destination is not None
    assert pin.destination.stop_id == 'rail:ELY'
    assert not pin.routes


def test_each_train_line_has_its_own_stations(norwich_built: Path) -> None:
    """Every train line lists stations; Liverpool Street is not Norwich."""
    with closing(sqlite3.connect(norwich_built)) as conn:
        listed = {row[0] for row in conn.execute(
            "SELECT DISTINCT line_key FROM line_stations"
            " WHERE line_key LIKE 'train:%'",
        )}
        kept = {row[0] for row in conn.execute(
            "SELECT route_key FROM chooser_routes WHERE kind = 'train'",
        )}
        on = {
            (row[0], row[1]) for row in conn.execute(
                "SELECT line_key, station_id FROM line_stations"
                " WHERE station_id = 'rail:LST'",
            )
        }
    assert len(kept) == 6
    assert listed == kept
    assert ('train:GA:Liverpool Street', 'rail:LST') in on
    assert ('train:GA:Norwich', 'rail:LST') not in on
