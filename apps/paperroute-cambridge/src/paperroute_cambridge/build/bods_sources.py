"""Cambridge's timetable sources: BODS East Anglia, then national rail."""

from typing import Final

from paperroute_cambridge.build.rail_cut import cut_rail
from paperroute_cambridge.city import RAIL_SOURCE
from paperroute_transit.build.sources import SourceSpec

BODS_TIMETABLES: Final[str] = (
    'https://data.bus-data.dft.gov.uk/timetable/download/gtfs-file/'
)
RAIL_TIMETABLE: Final[str] = (
    'https://github.com/planarnetwork/gb-transit/releases/latest/'
    'download/gtfs-national-rail-only.zip'
)
BODS_SIZE_LIMIT: Final[int] = 250_000_000  # bytes
RAIL_SIZE_LIMIT: Final[int] = 150_000_000  # bytes
CAMBRIDGE_SOURCES: Final[tuple[SourceSpec, ...]] = (
    SourceSpec(
        name='bods',
        path=f'{BODS_TIMETABLES}east_anglia/',
        size_limit=BODS_SIZE_LIMIT,
    ),
    SourceSpec(
        name=RAIL_SOURCE,
        path=RAIL_TIMETABLE,
        cut=cut_rail,
        optional=True,
        size_limit=RAIL_SIZE_LIMIT,
    ),
)
