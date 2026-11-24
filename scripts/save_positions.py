"""Save a live GTFS-RT vehicle-positions feed to a file."""

import argparse
import asyncio
import os
import sys
import urllib.error
import urllib.request
from collections.abc import Sequence
from pathlib import Path
from typing import Final

import aiohttp

from paperroute_cambridge.adapters import bods
from paperroute_cambridge.area import CAMBRIDGE_BBOX
from paperroute_transit.adapters.feed_errors import FeedError

TFNSW_URL: Final[str] = 'https://api.transport.nsw.gov.au/'
TFNSW_FEED: Final[str] = 'v1/gtfs/vehiclepos/buses'
ACCEPT: Final[str] = 'application/x-google-protobuf'
SOURCES: Final[tuple[str, ...]] = ('tfnsw', 'bods')
KEY_FILES: Final[dict[str, Path]] = {
    'tfnsw': Path('~/tfnsw_api_key'),
    'bods': Path('~/bods_api_key'),
}
TIMEOUT: Final[int] = 30  # seconds


def parser() -> argparse.ArgumentParser:
    """
    Build the command-line parser.

    Returns
    -------
    argparse.ArgumentParser
        Parser for the options.
    """
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument('--source', choices=SOURCES, default='tfnsw')
    result.add_argument('--out', type=Path, required=True)
    result.add_argument(
        '--feed', default=TFNSW_FEED, help='TfNSW feed path',
    )
    result.add_argument(
        '--key-file',
        type=Path,
        help="default: PAPERROUTE_KEY_FILE_<SOURCE>, else the source's file",
    )
    result.add_argument('--base-url', help="default: the source's API")
    return result


def read_key(*, source: str, given: Path | None) -> str:
    """
    Read a source's API key from its file.

    Parameters
    ----------
    source : str
        One of `SOURCES`.
    given : Path | None
        The file asked for, or None for the environment's or the
        default.

    Returns
    -------
    str
        The key; '' when the file is missing or blank.
    """
    variable = f'PAPERROUTE_KEY_FILE_{source.upper()}'
    path = given or Path(os.getenv(variable) or KEY_FILES[source])
    try:
        return path.expanduser().read_text(encoding='utf-8').strip()
    except (OSError, UnicodeDecodeError):
        return ''


def tfnsw_body(*, key: str, url: str) -> bytes:
    """
    Download a TfNSW positions feed.

    Parameters
    ----------
    key : str
        The API key.
    url : str
        The feed's full URL.

    Returns
    -------
    bytes
        The protobuf body.
    """
    request = urllib.request.Request(
        url,
        headers={'Authorization': f'apikey {key}', 'Accept': ACCEPT},
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return bytes(response.read())


async def bods_body(*, key: str, base_url: str) -> bytes:
    """
    Download the BODS positions in Cambridge's box.

    Parameters
    ----------
    key : str
        The API key.
    base_url : str
        API root, ending in '/'.

    Returns
    -------
    bytes
        The protobuf body, with coaches unnamed.
    """
    async with aiohttp.ClientSession() as session:
        request = bods.BodsRequest(
            session=session, key=key, box=CAMBRIDGE_BBOX, base_url=base_url,
        )
        return await bods.raw_positions(request=request)


def main(argv: Sequence[str] | None = None) -> int:
    """
    Download a positions feed with the key in a file and save it.

    Parameters
    ----------
    argv : Sequence[str] | None
        Arguments; the process's own when None.

    Returns
    -------
    int
        0 when saved; 1 when the key file is missing or blank or the
        download fails. The key is never printed.
    """
    args = parser().parse_args(argv)
    key = read_key(source=args.source, given=args.key_file)
    if not key:
        print(f'no {args.source} key found', file=sys.stderr)
        return 1
    try:
        if args.source == 'bods':
            data = asyncio.run(
                bods_body(key=key, base_url=args.base_url or bods.BASE_URL),
            )
        else:
            data = tfnsw_body(
                key=key, url=(args.base_url or TFNSW_URL) + args.feed,
            )
    except (urllib.error.URLError, TimeoutError, FeedError) as error:
        print(f'download failed: {type(error).__name__}', file=sys.stderr)
        return 1
    args.out.write_bytes(data)
    print(f'saved {len(data)} bytes to {args.out}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
