"""Web files for package tests that run without an app."""

from importlib.resources import files
from typing import Final

from paperroute_device.assets import AssetRoot

TEST_ASSETS: Final[AssetRoot] = AssetRoot(
    web=files('paperroute_device_testkit') / 'assets' / 'web',
)
