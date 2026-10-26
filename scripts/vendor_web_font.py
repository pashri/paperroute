"""Download Public Sans (Latin subset) and its OFL licence."""

import hashlib
import re
import sys
import urllib.request
from pathlib import Path
from typing import Final

CSS_URL: Final[str] = (
    'https://fonts.googleapis.com/css2?family=Public+Sans:wght@400..700'
    '&display=swap'
)
LICENCE: Final[str] = ('https://raw.githubusercontent.com/google/fonts/main/'
                       'ofl/publicsans/OFL.txt')
USER_AGENT: Final[str] = (
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36'
)
LATIN_BLOCK: Final[re.Pattern[str]] = re.compile(
    r'/\*\s*latin\s*\*/\s*@font-face\s*\{[^}]*?src:\s*url\(([^)]+)\)',
)


def latin_url(*, css: str) -> str:
    """
    Find the font address in the Latin block of a Google Fonts sheet.

    Parameters
    ----------
    css : str
        Stylesheet text from Google Fonts.

    Returns
    -------
    str
        The ``src`` URL of the ``@font-face`` after the ``/* latin */``
        comment.

    Raises
    ------
    ValueError
        If the sheet has no Latin block.
    """
    match = LATIN_BLOCK.search(css)
    if match is None:
        raise ValueError('no latin block')
    return match.group(1).strip('\'"')


def download(*, url: str) -> bytes:
    """
    Fetch a URL with a desktop browser user agent.

    Parameters
    ----------
    url : str
        Address to fetch.

    Returns
    -------
    bytes
        Response body.
    """
    request = urllib.request.Request(
        url,
        headers={'User-Agent': USER_AGENT},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
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
    path.write_bytes(data)
    print(f'{hashlib.sha256(data).hexdigest()}  {path.name}')


def main() -> None:
    """Write the font and licence into the directory given as argv[1]."""
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    css = download(url=CSS_URL).decode()
    font = download(url=latin_url(css=css))
    save(data=font, path=out / 'public-sans.woff2')
    save(data=download(url=LICENCE), path=out / 'OFL.txt')


if __name__ == '__main__':
    main()
