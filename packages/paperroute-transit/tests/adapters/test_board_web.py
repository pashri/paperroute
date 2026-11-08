"""Tests for finding the board's web files, app first, then transit."""

from pathlib import Path
from typing import Final

import pytest

from paperroute_device import assets as asset_module
from paperroute_device.assets import AssetRoot
from paperroute_testkit.paths import ROOT
from paperroute_transit.adapters.board_web import (
    BOARD_WEB,
    board_bytes,
    board_text,
)

BOARD_FILES: Final[tuple[str, ...]] = (
    'pins.html',
    'settings.html',
    'pin-edit.html',
    'board.css',
    'pins.js',
    'settings.js',
    'stop-search.js',
    'ways.js',
    'windows.js',
    'suggest.js',
    'vendor/tom-select.css',
    'vendor/tom-select.complete.min.js',
    'vendor/LICENSE-tom-select.txt',
)


def test_transit_ships_board_files() -> None:
    """Every board web file is in transit's own web directory."""
    assert Path(str(BOARD_WEB)) == (
        ROOT / 'packages/paperroute-transit/src/paperroute_transit/web'
    )
    assert [
        name
        for name in BOARD_FILES
        if not BOARD_WEB.joinpath(*name.split('/')).is_file()
    ] == []


def test_app_file_wins(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An app's own copy of a board file is read in place of transit's."""
    shipped = (BOARD_WEB / 'pins.js').read_text(encoding='utf-8')
    monkeypatch.setattr(asset_module, 'IN_USE', [AssetRoot(web=tmp_path)])
    assert board_text(name='pins.js') == shipped
    (tmp_path / 'pins.js').write_text('// app pins\n', encoding='utf-8')
    assert board_text(name='pins.js') == '// app pins\n'


def test_missing_name_raises() -> None:
    """A name in neither web directory raises FileNotFoundError."""
    with pytest.raises(FileNotFoundError):
        board_bytes(name='vendor/nope.js')
