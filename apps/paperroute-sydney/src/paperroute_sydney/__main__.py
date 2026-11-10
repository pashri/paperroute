"""Command line: run Sydney's board."""

from typing import Final

from paperroute_device.adapters.secrets_file import SecretsFile
from paperroute_device.core.device_model import DeviceKey
from paperroute_sydney.adapters.tfnsw import (
    TFNSW_DISPLAY_NAME,
    TFNSW_KEY,
    TFNSW_KEY_NAME,
    AiohttpFeedsClient,
    key_path_from_env,
    tfnsw_limiter,
)
from paperroute_sydney.adapters.web_board import (
    SYDNEY_HINT,
    SYDNEY_NAV,
    sydney_status_line,
    sydney_suggest,
)
from paperroute_sydney.asset_root import APP_ASSETS
from paperroute_sydney.city import SYDNEY
from paperroute_transit import board
from paperroute_transit.city_parts import CityParts
from paperroute_transit.ports import FeedsClient


def tfnsw_feeds() -> FeedsClient:
    """
    Make the client of TfNSW's live feeds.

    It reads the key from PAPERROUTE_KEY_FILE_TFNSW at each fetch, so a
    changed key is used without a restart, and waits on the limiter the
    Trip Planner shares.

    Returns
    -------
    FeedsClient
        The live client.
    """
    return AiohttpFeedsClient(
        key=SecretsFile(path=key_path_from_env()).read,
        limiter=tfnsw_limiter(),
    )


def tfnsw_keys() -> tuple[DeviceKey, ...]:
    """
    Give the one API key Sydney's device keeps.

    Returns
    -------
    tuple[DeviceKey, ...]
        The TfNSW key, in the file PAPERROUTE_KEY_FILE_TFNSW names. The
        board says when it was last used.
    """
    return (
        DeviceKey(
            name=TFNSW_KEY_NAME,
            credential=TFNSW_KEY,
            store=SecretsFile(path=key_path_from_env()),
            display_name=TFNSW_DISPLAY_NAME,
        ),
    )


SYDNEY_PARTS: Final[CityParts] = CityParts(
    assets=APP_ASSETS,
    live_feeds=tfnsw_feeds,
    keys=tfnsw_keys,
    nav=SYDNEY_NAV,
    hint=SYDNEY_HINT,
    suggest=sydney_suggest,
    status_line=sydney_status_line,
    sheets=('palette.css',),
)


def main() -> None:
    """Run Sydney's board in a window or in browsers."""
    board.main(city=SYDNEY, parts=SYDNEY_PARTS)


if __name__ == '__main__':
    main()
