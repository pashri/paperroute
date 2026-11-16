"""Command line: run Cambridge's board."""

from typing import Final

from paperroute_cambridge.adapters.bods import (
    BODS_DISPLAY_NAME,
    BODS_KEY,
    BODS_KEY_NAME,
    BodsFeedsClient,
    key_path_from_env,
)
from paperroute_cambridge.adapters.web_board import (
    CAMBRIDGE_CREDITS,
    CAMBRIDGE_HINT,
    CAMBRIDGE_NAV,
    cambridge_status_line,
    cambridge_suggest,
)
from paperroute_cambridge.area import CAMBRIDGE_BBOX
from paperroute_cambridge.asset_root import APP_ASSETS
from paperroute_cambridge.city import CAMBRIDGE
from paperroute_device.adapters.secrets_file import SecretsFile
from paperroute_device.assets import use_assets
from paperroute_device.core.device_model import DeviceKey
from paperroute_transit import board
from paperroute_transit.city_parts import CityParts
from paperroute_transit.ports import FeedsClient


def bods_feeds() -> FeedsClient:
    """
    Make the client of the BODS live feeds.

    It reads the key from PAPERROUTE_KEY_FILE_BODS at each fetch, so a
    changed key is used without a restart.

    Returns
    -------
    FeedsClient
        The live client.
    """
    return BodsFeedsClient(
        key=SecretsFile(path=key_path_from_env()).read,
        box=CAMBRIDGE_BBOX,
    )


def bods_keys() -> tuple[DeviceKey, ...]:
    """
    Give the one API key Cambridge's device keeps.

    Returns
    -------
    tuple[DeviceKey, ...]
        The BODS key, in the file PAPERROUTE_KEY_FILE_BODS names.
    """
    return (
        DeviceKey(
            name=BODS_KEY_NAME,
            credential=BODS_KEY,
            store=SecretsFile(path=key_path_from_env()),
            display_name=BODS_DISPLAY_NAME,
        ),
    )


CAMBRIDGE_PARTS: Final[CityParts] = CityParts(
    assets=APP_ASSETS,
    live_feeds=bods_feeds,
    keys=bods_keys,
    nav=CAMBRIDGE_NAV,
    hint=CAMBRIDGE_HINT,
    suggest=cambridge_suggest,
    status_line=cambridge_status_line,
    sheets=('palette.css',),
    about='Buses and coaches',
    rebuild_minutes=10,
    credits=CAMBRIDGE_CREDITS,
)


def main() -> None:
    """Run Cambridge's board in a window or in browsers."""
    use_assets(root=CAMBRIDGE_PARTS.assets)
    board.main(city=CAMBRIDGE, parts=CAMBRIDGE_PARTS)


if __name__ == '__main__':
    main()
