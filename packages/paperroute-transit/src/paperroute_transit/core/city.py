"""The values that fit the engine to one city."""

import re
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol
from zoneinfo import ZoneInfo

from paperroute_device.core.device_settings import Window
from paperroute_transit.core.geo import DegreeBox, Projection
from paperroute_transit.core.model import Pin
from paperroute_transit.core.modes import Classifier, ModeSpec, spec_of


@dataclass(frozen=True)
class PlaceNames:
    """
    How route place tags are read and shortened.

    Attributes
    ----------
    centres : tuple[tuple[str, float, float, float], ...]
        Big centres as label, longitude, latitude and radius in km; a
        route whose middle lies within one is tagged with its label.
    suffix : re.Pattern[str]
        Matches the endings cut from a place name, such as ' Station'.
    short : Mapping[str, str]
        Short forms of long place names.
    """

    centres: tuple[tuple[str, float, float, float], ...]
    suffix: re.Pattern[str]
    short: Mapping[str, str]


class LineGroup(Protocol):
    """Sorts a line number into the group it is listed in."""

    def __call__(self, *, number: str) -> int:
        """
        Group one line.

        Parameters
        ----------
        number : str
            Line number.

        Returns
        -------
        int
            Group; lines are listed group by group, lowest first.
        """


@dataclass(frozen=True)
class CatalogueRules:
    """
    How the route chooser's catalogue is built and listed.

    Attributes
    ----------
    line_sort : Mapping[str, int]
        Fixed rank of each known line number of the mode replacement
        routes stand in for; other lines rank after them.
    replacement : re.Pattern[str]
        Matches a replacement route number; its first group names the
        line it stands in for.
    replacement_lines : Mapping[str, str]
        Line number by the code a replacement route number uses for it,
        where the two differ.
    numbered_lines : re.Pattern[str]
        Matches the wharf-named mode's lines ranked by the number after
        their first letter; others rank after them.
    rail_types : frozenset[int]
        GTFS route types run on rails.
    line_group : LineGroup
        The group a line is listed in.
    places : PlaceNames
        How route place tags are read and shortened.
    """

    line_sort: Mapping[str, int]
    replacement: re.Pattern[str]
    replacement_lines: Mapping[str, str]
    numbered_lines: re.Pattern[str]
    rail_types: frozenset[int]
    line_group: LineGroup
    places: PlaceNames


@dataclass(frozen=True)
class MergeReport:
    """
    What merging a city's duplicate trips did.

    Attributes
    ----------
    dropped : int
        Trips dropped.
    partial : int
        Trips matched on only some of their dates.
    overnight : int
        Trips dropped or skipped as overnight twins.
    """

    dropped: int
    partial: int
    overnight: int


class MergeHook(Protocol):
    """Merges the duplicate trips of a timetable whose sources are loaded."""

    def __call__(self, *, conn: sqlite3.Connection) -> MergeReport:
        """
        Merge one timetable.

        Parameters
        ----------
        conn : sqlite3.Connection
            Timetable being built, every source loaded.

        Returns
        -------
        MergeReport
            What the merge did.
        """


@dataclass(frozen=True)
class TimetableRules:
    """
    How a city's timetable is built.

    Attributes
    ----------
    merge : MergeHook
        Runs once every source is loaded.
    holiday_source : str
        Name of the source whose calendar gives the public holidays.
    """

    merge: MergeHook
    holiday_source: str


@dataclass(frozen=True)
class AgencyAlerts:
    """
    Agencies whose alerts come from a feed source of their own.

    Attributes
    ----------
    agencies : frozenset[str]
        GTFS agency IDs.
    modes : frozenset[str]
        Mode ids of the pins that may ride them; a pin of no kind may too.
    source : str
        The feed source whose alert feeds such a pin also asks.
    """

    agencies: frozenset[str]
    modes: frozenset[str]
    source: str


@dataclass(frozen=True)
class AlertRules:
    """
    How alerts are tied to stops and routes, by feed source.

    Attributes
    ----------
    name_suffixes : tuple[str, ...]
        Endings cut from a stop name before looking for it in alert text.
    name_sources : frozenset[str]
        Feed sources whose alerts name stations.
    loose_sources : frozenset[str]
        Feed sources whose alerts match loosely.
    wharf_sources : frozenset[str]
        Feed sources whose alerts name stops by wharf.
    route_prefixes : tuple[str, ...]
        Prefixes tried, in order, to find an alert's route ID in the
        timetable.
    agency_alerts : AgencyAlerts | None
        Agencies alerted through a feed source of their own, or None.
    """

    name_suffixes: tuple[str, ...]
    name_sources: frozenset[str]
    loose_sources: frozenset[str]
    wharf_sources: frozenset[str]
    route_prefixes: tuple[str, ...]
    agency_alerts: AgencyAlerts | None


@dataclass(frozen=True)
class Nouns:
    """
    The words a departure's mode is written with.

    Attributes
    ----------
    vehicle : str
        What is ridden, as in 'NEXT TRAIN' and 'min on the train'.
    bay : str
        Where it is boarded, as in 'platform 3'.
    connection : str
        What a connection's ride is called.
    """

    vehicle: str
    bay: str
    connection: str


@dataclass(frozen=True)
class City:
    """
    One city's settings.

    Attributes
    ----------
    name : str
        The city's name.
    zone : ZoneInfo
        Local time zone: clocks, service days and auto windows use it.
    projection : Projection
        The metre projection and pixel grid for its maps.
    bbox : DegreeBox
        The area its tiles may cover.
    modes : tuple[ModeSpec, ...]
        Its modes, in chooser order.
    classify : Classifier
        Sorts a route into a mode and a feed source.
    catalogue : CatalogueRules
        How the route chooser's catalogue is built and listed.
    timetable : TimetableRules
        How its timetable is built.
    alerts : AlertRules
        How alerts are tied to stops and routes.
    default_pin : Pin
        The place shown when there is nothing else to show.
    peak_windows : tuple[Window, ...]
        The weekday peak windows a new board starts with.
    source_nouns : Mapping[str, Nouns]
        The words for a departure, by feed source.
    noun_overrides : Mapping[tuple[str, str], Nouns]
        The words for a departure by feed source and mode id, ahead of
        `source_nouns`.
    """

    name: str
    zone: ZoneInfo
    projection: Projection
    bbox: DegreeBox
    modes: tuple[ModeSpec, ...]
    classify: Classifier
    catalogue: CatalogueRules
    timetable: TimetableRules
    alerts: AlertRules
    default_pin: Pin
    peak_windows: tuple[Window, ...]
    source_nouns: Mapping[str, Nouns]
    noun_overrides: Mapping[tuple[str, str], Nouns]

    def mode(self, *, mode_id: str | None) -> ModeSpec | None:
        """
        Find one of the city's modes.

        Parameters
        ----------
        mode_id : str | None
            Mode id, or None.

        Returns
        -------
        ModeSpec | None
            Its spec; None for None or an id the city does not declare.
        """
        return spec_of(modes=self.modes, mode=mode_id)

    @property
    def mode_ids(self) -> frozenset[str]:
        """
        The ids of the city's modes.

        Returns
        -------
        frozenset[str]
            Every declared mode id.
        """
        return frozenset(spec.id for spec in self.modes)
