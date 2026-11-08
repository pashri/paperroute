"""Tests for the asset root the app installs."""

import subprocess
import sys
from pathlib import Path
from typing import Final

import pytest

from paperroute_device import assets as assets_module
from paperroute_device.assets import AssetRoot, assets, use_assets

NO_ROOT: Final[str] = (
    'from paperroute_device.assets import assets\n'
    'from paperroute_device.render.canvas import Face, font\n'
    'print(font(Face(weight=400, px=16)).getTypeface().getFamilyName())\n'
    'try:\n'
    '    assets()\n'
    'except RuntimeError as error:\n'
    '    print(error)\n'
)


def test_font_needs_no_asset_root() -> None:
    """
    Overpass loads with no asset root set; the root is still unset.

    Skia falls back to a system face without raising when a font file
    is missing, so the family name is checked.
    """
    result = subprocess.run(
        [sys.executable, '-c', NO_ROOT],
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout.splitlines() == [
        'Overpass',
        'no asset root: the app must call use_assets first',
    ]


def test_use_assets_rejects_a_second_root(tmp_path: Path) -> None:
    """A different root after the first is refused."""
    other = AssetRoot(web=tmp_path)
    with pytest.raises(ValueError):
        use_assets(root=other)
    assert assets() != other


def test_use_assets_accepts_the_same_root() -> None:
    """Setting the root in use again changes nothing."""
    root = assets()
    use_assets(root=AssetRoot(web=root.web))
    assert assets() == root


def test_assets_without_root_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """Asking for the root before one is set says what to do."""
    monkeypatch.setattr(assets_module, 'IN_USE', [])
    with pytest.raises(RuntimeError, match='must call use_assets first'):
        assets()
