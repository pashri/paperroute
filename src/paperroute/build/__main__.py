"""Build Sydney's timetable and map tiles."""

from typing import Final

from paperroute.asset_root import use_app_assets
from paperroute.build import cli
from paperroute.build.sources import BuildParts
from paperroute.build.tfnsw_sources import SYDNEY_SOURCES, TfnswDownloader
from paperroute.city import SYDNEY, SYDNEY_REF

SYDNEY_BUILD: Final[BuildParts] = BuildParts(
    city_ref=SYDNEY_REF,
    sources=SYDNEY_SOURCES,
    downloader=TfnswDownloader(),
)


def main() -> None:
    """Run the build command line for Sydney."""
    use_app_assets()
    cli.main(city=SYDNEY, parts=SYDNEY_BUILD)


if __name__ == '__main__':
    main()
