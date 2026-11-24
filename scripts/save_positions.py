"""Save a live GTFS-RT vehicle-positions feed to a file."""

import argparse
import sys
import urllib.error
import urllib.request
from collections.abc import Sequence
from pathlib import Path
from typing import Final

BASE_URL: Final[str] = 'https://api.transport.nsw.gov.au/'
DEFAULT_FEED: Final[str] = 'v1/gtfs/vehiclepos/buses'
DEFAULT_KEY: Final[Path] = Path.home() / 'tfnsw_api_key'
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
    result.add_argument('--out', type=Path, required=True)
    result.add_argument('--feed', default=DEFAULT_FEED)
    result.add_argument('--key-file', type=Path, default=DEFAULT_KEY)
    result.add_argument('--base-url', default=BASE_URL)
    return result


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
    try:
        key = args.key_file.read_text(encoding='utf-8').strip()
    except (OSError, UnicodeDecodeError):
        key = ''
    if not key:
        print(f'no TfNSW key in {args.key_file}', file=sys.stderr)
        return 1
    request = urllib.request.Request(
        args.base_url + args.feed,
        headers={
            'Authorization': f'apikey {key}',
            'Accept': 'application/x-google-protobuf',
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            data = response.read()
    except (urllib.error.URLError, TimeoutError) as error:
        print(f'download failed: {type(error).__name__}', file=sys.stderr)
        return 1
    args.out.write_bytes(data)
    print(f'saved {len(data)} bytes to {args.out}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
