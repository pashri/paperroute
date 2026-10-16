"""Download Overpass and save TTF copies plus the OFL licence."""

import io
import sys
import urllib.request
from pathlib import Path
from typing import Final

from fontTools.ttLib import TTFont

WOFF: Final[str] = ('https://cdn.jsdelivr.net/npm/@fontsource/overpass@5/files/'
                    'overpass-latin-{weight}-normal.woff')
LICENCE: Final[str] = ('https://raw.githubusercontent.com/google/fonts/main/'
                       'ofl/overpass/OFL.txt')
WEIGHTS: Final[tuple[int, ...]] = (400, 700, 800)


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


def save_ttf(*, weight: int, out: Path) -> None:
    """
    Convert one Overpass weight from WOFF to TTF.

    Parameters
    ----------
    weight : int
        Font weight.
    out : Path
        Directory for the TTF file.
    """
    font = TTFont(io.BytesIO(download(url=WOFF.format(weight=weight))))
    font.flavor = None
    font.save(out / f'Overpass-{weight}.ttf')


def main() -> None:
    """Write the fonts and licence into the directory given as argv[1]."""
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    for weight in WEIGHTS:
        save_ttf(weight=weight, out=out)
    (out / 'OFL.txt').write_bytes(download(url=LICENCE))


if __name__ == '__main__':
    main()
