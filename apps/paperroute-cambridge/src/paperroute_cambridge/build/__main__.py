"""Build Cambridge's timetable and map tiles."""

from typing import Final

from paperroute_cambridge.build.bods_sources import (
    BODS_TIMETABLES,
    CAMBRIDGE_SOURCES,
)
from paperroute_cambridge.city import CAMBRIDGE, CAMBRIDGE_REF
from paperroute_transit.build import cli
from paperroute_transit.build.sources import BuildParts, UrlDownloader

CAMBRIDGE_BUILD: Final[BuildParts] = BuildParts(
    city_ref=CAMBRIDGE_REF,
    sources=CAMBRIDGE_SOURCES,
    downloader=UrlDownloader(base_url=BODS_TIMETABLES),
)


def main() -> None:
    """Run the build command line for Cambridge."""
    cli.main(city=CAMBRIDGE, parts=CAMBRIDGE_BUILD)


if __name__ == '__main__':
    main()
