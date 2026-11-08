"""Command line: run Sydney's board."""

from typing import Final

from paperroute import board
from paperroute.adapters.tfnsw import (
    TFNSW_KEY,
    AiohttpFeedsClient,
    key_path_from_env,
    tfnsw_limiter,
)
from paperroute.adapters.web_board import (
    SYDNEY_HINT,
    SYDNEY_NAV,
    sydney_status_line,
    sydney_suggest,
)
from paperroute.asset_root import APP_ASSETS
from paperroute.city import SYDNEY
from paperroute.ports import FeedsClient
from paperroute_device.adapters.secrets_file import SecretsFile
from paperroute_device.core.device_model import DeviceKey


def tfnsw_feeds() -> FeedsClient:
    """
    Make the client of TfNSW's live feeds.

    It reads the key from PAPERROUTE_API_KEY_FILE at each fetch, so a
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
        The TfNSW key, in the file PAPERROUTE_API_KEY_FILE names.
    """
    return (
        DeviceKey(
            name='tfnsw',
            credential=TFNSW_KEY,
            store=SecretsFile(path=key_path_from_env()),
        ),
    )


SYDNEY_PARTS: Final[board.CityParts] = board.CityParts(
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
