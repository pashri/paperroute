"""Download one web asset and print its SHA-256."""

import hashlib
import sys
import urllib.request
from pathlib import Path


def download(*, url: str) -> bytes:
    """
    Fetch a URL.

    Parameters
    ----------
    url : str
        Address to fetch.

    Returns
    -------
    bytes
        Response body.
    """
    with urllib.request.urlopen(url, timeout=30) as response:
        return bytes(response.read())


def save(*, data: bytes, path: Path) -> None:
    """
    Write a file and print its SHA-256.

    Parameters
    ----------
    data : bytes
        File contents.
    path : Path
        Destination file.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    print(f'{hashlib.sha256(data).hexdigest()}  {path.name}')


def main() -> None:
    """Fetch argv[1] (a URL) into argv[2] (a file path)."""
    save(data=download(url=sys.argv[1]), path=Path(sys.argv[2]))


if __name__ == '__main__':
    main()
