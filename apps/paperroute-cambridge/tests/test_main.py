"""Tests for the Cambridge board command."""

from datetime import datetime
from pathlib import Path

import pytest

from paperroute_cambridge.__main__ import CAMBRIDGE_PARTS, bods_keys
from paperroute_cambridge.adapters.bods import BODS_KEY
from paperroute_cambridge.city import CAMBRIDGE
from paperroute_device.core.text import DOT
from paperroute_transit import board
from paperroute_transit.transit_app import TransitApp


def test_card_describes_cambridge(tmp_path: Path) -> None:
    """Card describes cambridge."""
    card = TransitApp(city=CAMBRIDGE, parts=CAMBRIDGE_PARTS).card(
        data_dir=tmp_path,
        now=datetime(2026, 11, 16, 9, 0, tzinfo=CAMBRIDGE.zone),
    )
    assert card.name == 'Cambridge'
    assert card.status == f'Buses and coaches{DOT}no timetable yet'
    assert 'for up to 40 minutes' in card.wait


def test_keys_are_the_one_bus_data_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Keys are the one bus data key."""
    monkeypatch.setenv('PAPERROUTE_KEY_FILE_BODS', str(tmp_path / 'secrets'))
    (key,) = bods_keys()
    assert (key.name, key.display_name, key.credential) == (
        'bods', 'Bus data', BODS_KEY,
    )
    assert BODS_KEY.label == 'Bus data API key'


def test_zone_command_prints_europe_london(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Zone command prints europe london."""
    board.main(city=CAMBRIDGE, parts=CAMBRIDGE_PARTS, argv=['zone'])
    assert capsys.readouterr().out == 'Europe/London\n'
