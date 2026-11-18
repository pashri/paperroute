"""Cambridge's timetable sources: BODS East Anglia, then national rail."""

from typing import Final

from paperroute_cambridge.build.rail_cut import cut_rail
from paperroute_transit.build.sources import SourceSpec

BODS_TIMETABLES: Final[str] = (
    'https://data.bus-data.dft.gov.uk/timetable/download/gtfs-file/'
)
RAIL_TIMETABLE: Final[str] = (
    'https://github.com/planarnetwork/gb-transit/releases/latest/'
    'download/gtfs-national-rail-only.zip'
)
CAMBRIDGE_SOURCES: Final[tuple[SourceSpec, ...]] = (
    SourceSpec(name='bods', path=f'{BODS_TIMETABLES}east_anglia/'),
    SourceSpec(
        name='rail',
        path=RAIL_TIMETABLE,
        cut=cut_rail,
        optional=True,
    ),
)
