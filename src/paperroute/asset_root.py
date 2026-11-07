"""The app's fonts and web files, as the device kit reads them."""

from importlib.resources import files
from typing import Final

from paperroute_device.assets import AssetRoot, use_assets

APP_ASSETS: Final[AssetRoot] = AssetRoot(
    fonts=files('paperroute') / 'assets' / 'fonts',
    web=files('paperroute') / 'assets' / 'web',
)


def use_app_assets() -> None:
    """Point the device kit at the app's package data."""
    use_assets(root=APP_ASSETS)
