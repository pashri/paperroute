"""Cambridge's timetable source: the BODS East Anglia GTFS file."""

from typing import Final

from paperroute_transit.build.sources import SourceSpec

BODS_TIMETABLES: Final[str] = (
    'https://data.bus-data.dft.gov.uk/timetable/download/gtfs-file/'
)
CAMBRIDGE_SOURCES: Final[tuple[SourceSpec, ...]] = (
    SourceSpec(name='bods', path='east_anglia/'),
)
