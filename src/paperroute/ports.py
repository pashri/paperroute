"""Interfaces the core and app depend on; adapters implement them."""

# pylint: disable=too-many-lines

from collections.abc import Collection
from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol

import numpy as np
from numpy.typing import NDArray

from paperroute.core.alert_model import AlertPlaces
from paperroute.core.chooser import Choice, ChooserPath, Column
from paperroute.core.device_model import (
    DeviceFacts,
    DeviceStatus,
    JoinOutcome,
    JoinRequest,
    ResetKind,
    WifiNetwork,
    WifiStatus,
)
from paperroute.core.geo import MetreBox, Shape
from paperroute.core.messages import PowerAction
from paperroute.core.model import (
    Departure,
    Mode,
    Pin,
    RouteKind,
    ServiceSpan,
    StopPlace,
)
from paperroute.core.patterns import TripPattern
from paperroute.core.pin_choices import ChangeStop, StopMatch
from paperroute.core.reach import ReachInput, StripKey
from paperroute.core.route_colours import RouteColour
from paperroute.core.saved import CLEAN_AFTER, Saved, Settings
from paperroute.core.tiles import LabelKind, StreetLabel, TileVariant


class TimetableRepository(Protocol):
    """Read access to the built timetable."""

    def stop(self, *, stop_id: str) -> StopPlace:
        """
        A stop or station with its position.

        Parameters
        ----------
        stop_id : str
            Stop or station ID.

        Returns
        -------
        StopPlace
            Name and point; the point is None without coordinates.

        Raises
        ------
        KeyError
            When the stop isn't in the timetable.
        """

    def match_stop(self, *, place: StopPlace) -> StopPlace | None:
        """
        Find a place again in the current timetable.

        Parameters
        ----------
        place : StopPlace
            A place from an earlier timetable.

        Returns
        -------
        StopPlace | None
            The stop with the same ID; else the nearest stop of the same
            name within 100 m of the place's point; else None.
        """

    def version(self) -> str | None:
        """
        Say which build the file is.

        Returns
        -------
        str | None
            The build time, or None when there is none.
        """

    def schema_version(self) -> str | None:
        """
        Say which schema the file is built to.

        Returns
        -------
        str | None
            The schema version, or None when the file records none.
        """

    def holidays(self) -> frozenset[date]:
        """
        The public holidays the timetable records.

        Returns
        -------
        frozenset[date]
            The holidays; none for a timetable built without them.
        """

    def service_span(self, *, pin: Pin, day: date) -> ServiceSpan | None:
        """
        When a pin's trips run on a service day.

        Parameters
        ----------
        pin : Pin
            From and Destination.
        day : date
            Service day.

        Returns
        -------
        ServiceSpan | None
            From the earliest first-stop departure to the latest
            last-stop arrival of the pin's trips that day; None when
            there are none.
        """

    def shapes(self, *, shape_ids: Collection[str]) -> dict[str, Shape]:
        """
        Route shapes by ID.

        Parameters
        ----------
        shape_ids : Collection[str]
            Namespaced shape IDs.

        Returns
        -------
        dict[str, Shape]
            The shapes found; unknown IDs are absent.
        """

    def patterns(
        self, *, trip_ids: Collection[str], around: date
    ) -> dict[str, TripPattern]:
        """
        Stops, shape and running dates of trips.

        Parameters
        ----------
        trip_ids : Collection[str]
            Trip IDs or aliases.
        around : date
            Service date; the day before is included as well.

        Returns
        -------
        dict[str, TripPattern]
            Patterns by requested ID; unknown trips are absent.
        """

    def route_labels(self, *, trip_ids: Collection[str]) -> dict[str, str]:
        """
        Route short names by trip or alias.

        Parameters
        ----------
        trip_ids : Collection[str]
            Trip IDs or aliases.

        Returns
        -------
        dict[str, str]
            Short name by ID; unknown IDs are absent.
        """

    def alert_places(
        self, *, route_ids: Collection[str], stop_ids: Collection[str]
    ) -> AlertPlaces:
        """
        Look up the routes and stops an alert feed names.

        Parameters
        ----------
        route_ids : Collection[str]
            Route IDs from the feed.
        stop_ids : Collection[str]
            Stop IDs from the feed.

        Returns
        -------
        AlertPlaces
            Route short names, stop families and the metro line; unknown
            IDs are absent.
        """

    def leg_lines(
        self, *, ends: tuple[str, str], day: date,
    ) -> frozenset[str]:
        """
        List the routes with a trip from one stop to another on a day.

        Parameters
        ----------
        ends : tuple[str, str]
            From and To stop or station IDs.
        day : date
            Service date.

        Returns
        -------
        frozenset[str]
            Timetable route IDs.
        """

    def serves_trainlink(self, *, stop_ids: Collection[str]) -> bool:
        """
        Say whether an NSW TrainLink trip calls at any of some stops.

        Parameters
        ----------
        stop_ids : Collection[str]
            Stop IDs.

        Returns
        -------
        bool
            True when one does.
        """

    def modes_near(self, *, box: MetreBox) -> frozenset[Mode]:
        """
        List the modes of the shapes that overlap an area.

        Parameters
        ----------
        box : MetreBox
            West, south, east and north in metres.

        Returns
        -------
        frozenset[Mode]
            The modes found.
        """

    def departures(
        self, *, pin: Pin, start: datetime, end: datetime
    ) -> list[Departure]:
        """
        Direct services leaving the pin's From between two moments.

        Parameters
        ----------
        pin : Pin
            From and Destination.
        start : datetime
            Earliest departure.
        end : datetime
            Latest departure.

        Returns
        -------
        list[Departure]
            In departure order. Without a Destination, every departure
            from the From, each running to its last stop.
        """


class NetworkRepository(Protocol):
    """Read access to the route chooser catalogue and the station runs."""

    def choices(self, *, path: ChooserPath) -> Column | None:
        """
        The chooser column that follows a path.

        A Train, Metro, Light rail or Ferry line opens its STATION
        column, and a station on it the TO column; both are None on a
        timetable without the line station tables.

        Parameters
        ----------
        path : ChooserPath
            Steps chosen so far; () for the KIND column.

        Returns
        -------
        Column | None
            The next column; None for an old timetable, an unknown path
            or a path with nothing after it.
        """

    def towards_line(self, *, towards: int) -> tuple[StopPlace, ...] | None:
        """
        The stations of a towards row's busiest stop sequence.

        Parameters
        ----------
        towards : int
            Towards ID.

        Returns
        -------
        tuple[StopPlace, ...] | None
            Stations in travel order with their names and points; None for
            an old timetable or an unknown towards ID.
        """

    def reach_input(
        self, *, key: StripKey, origin: StopPlace,
    ) -> ReachInput | None:
        """
        Gather the runs through a strip's From and the places they reach.

        Parameters
        ----------
        key : StripKey
            The From and which runs feed the strip.
        origin : StopPlace
            The From place.

        Returns
        -------
        ReachInput | None
            The From's station, its tails, their places and stops; None
            for an old timetable.
        """


class PinOptions(Protocol):
    """The choices a pin's editor offers, read from the timetable."""

    def search(
        self, *, text: str, skip_school: bool = False,
    ) -> tuple[StopMatch, ...]:
        """
        Find stops and stations that runs serve by name.

        Parameters
        ----------
        text : str
            Part of a name.
        skip_school : bool
            Whether school routes are left out of each match's routes.

        Returns
        -------
        tuple[StopMatch, ...]
            Matches, best first; empty for text that is too short.
        """

    def place(self, *, stop_id: str) -> StopPlace | None:
        """
        Read a stop or station.

        Parameters
        ----------
        stop_id : str
            Stop or station ID.

        Returns
        -------
        StopPlace | None
            The place; None when the timetable does not know it.
        """

    def trip_stop(self, *, stop_id: str) -> StopPlace | None:
        """
        Map a Trip Planner stop to the editor's station-level place.

        Parameters
        ----------
        stop_id : str
            Trip Planner stop ID.

        Returns
        -------
        StopPlace | None
            The stop, lifted to its parent station; None when unknown.
        """

    def kinds_at(self, *, stop_id: str) -> tuple[RouteKind, ...]:
        """
        List the kinds of route that have a line or route at a stop.

        Parameters
        ----------
        stop_id : str
            Stop or station ID.

        Returns
        -------
        tuple[RouteKind, ...]
            Kinds in display order.
        """

    def lines_at(self, *, stop_id: str, kind: RouteKind) -> tuple[Choice, ...]:
        """
        List the lines or routes of a kind that serve a stop.

        Parameters
        ----------
        stop_id : str
            Stop or station ID.
        kind : RouteKind
            Kind of route.

        Returns
        -------
        tuple[Choice, ...]
            Choices whose badge is the route number, in chooser order.
        """

    def destinations(
        self, *, origin: StopPlace, kind: RouteKind, line: Choice,
    ) -> tuple[StopPlace, ...]:
        """
        List the places the device offers as the To.

        Parameters
        ----------
        origin : StopPlace
            The From.
        kind : RouteKind
            Kind of route.
        line : Choice
            The line or route, from `lines_at`.

        Returns
        -------
        tuple[StopPlace, ...]
            Places in the device's list order; empty for an old
            timetable.
        """

    def filters_at(
        self, *, stop_id: str, kind: RouteKind,
    ) -> tuple[str, ...]:
        """
        List the route numbers a pin's departures can be limited to.

        Parameters
        ----------
        stop_id : str
            Stop or station ID.
        kind : RouteKind
            Kind of route.

        Returns
        -------
        tuple[str, ...]
            Route numbers of every run that boards at the stop.
        """

    def nearby(
        self, *, place: StopPlace, kind: RouteKind | None,
    ) -> tuple[StopPlace, ...]:
        """
        List stops close to a place that runs of a kind serve.

        Parameters
        ----------
        place : StopPlace
            The place, such as a missing pin's old stop.
        kind : RouteKind | None
            Kind of route, or None for any.

        Returns
        -------
        tuple[StopPlace, ...]
            Stops, nearest first; empty when the place has no point.
        """

    def change_stops(self, *, place: StopPlace) -> tuple[ChangeStop, ...]:
        """
        List the stops a connection can change to from a place.

        Parameters
        ----------
        place : StopPlace
            The place leg 1 is left at.

        Returns
        -------
        tuple[ChangeStop, ...]
            Stops within 500 m, the place included, nearest first.
        """

    def reached_from(self, *, place: StopPlace) -> tuple[StopPlace, ...]:
        """
        List the stations a run calls at after a place.

        Parameters
        ----------
        place : StopPlace
            The place leg 2 is joined at.

        Returns
        -------
        tuple[StopPlace, ...]
            Stations, by name.
        """

    def routes_between(
        self, *, origin: StopPlace, destination: StopPlace,
    ) -> tuple[str, ...]:
        """
        List the routes that run from one place to another.

        Parameters
        ----------
        origin : StopPlace
            Place boarded at.
        destination : StopPlace
            Place reached.

        Returns
        -------
        tuple[str, ...]
            Route numbers, in chooser order.
        """

    def route_colours(
        self, *, names: Collection[str],
    ) -> dict[str, RouteColour]:
        """
        Read the colours of routes by short name.

        Parameters
        ----------
        names : Collection[str]
            Route short names.

        Returns
        -------
        dict[str, RouteColour]
            Colours by name, only for names with a valid colour; empty
            for an old timetable.
        """


class TileRepository(Protocol):
    """Read access to the built map tiles."""

    def tile(
        self, *, zoom: int, col: int, row: int, variant: TileVariant
    ) -> NDArray[np.uint8] | None:
        """
        Read one tile.

        Parameters
        ----------
        zoom : int
            Zoom.
        col : int
            Tile column.
        row : int
            Tile row.
        variant : TileVariant
            Which rendering to read.

        Returns
        -------
        NDArray[np.uint8] | None
            The tile's pixels, or None when it isn't stored.
        """

    def street_labels(
        self,
        *,
        zoom: int,
        box: tuple[float, float, float, float],
        kind: LabelKind = LabelKind.STREET,
    ) -> list[StreetLabel]:
        """
        Find street labels near a pixel box.

        Parameters
        ----------
        zoom : int
            Zoom.
        box : tuple[float, float, float, float]
            Left, top, right and bottom in global pixels.
        kind : LabelKind
            Which labels; street names unless asked.

        Returns
        -------
        list[StreetLabel]
            Matching street labels.
        """

    def version(self) -> str | None:
        """
        Say which build the file is.

        Returns
        -------
        str | None
            The build time, or None when there is no file.
        """


class TimetableBuilder(Protocol):
    """Rebuilds the timetable file in the background."""

    async def rebuild(self, *, force: bool = False) -> bool:
        """
        Rebuild the timetable and wait for it to finish.

        Parameters
        ----------
        force : bool
            Build even when the timetable is up to date.

        Returns
        -------
        bool
            True when the rebuild succeeded.
        """

    async def stop(self) -> None:
        """End a rebuild still running."""


class PowerControl(Protocol):
    """Shuts the device down or restarts it."""

    async def control(self, *, action: PowerAction) -> bool:
        """
        Carry out a power action.

        Parameters
        ----------
        action : PowerAction
            Shut down or restart.

        Returns
        -------
        bool
            True when the action was accepted.
        """


class WifiControl(Protocol):
    """Scans for, joins and shares Wi-Fi."""

    async def status(self) -> WifiStatus:
        """
        Report the Wi-Fi link.

        Returns
        -------
        WifiStatus
            The joined network, signal and address; not managed when
            Wi-Fi can't be controlled.
        """

    async def scan(self) -> tuple[WifiNetwork, ...]:
        """
        List the networks in range.

        Returns
        -------
        tuple[WifiNetwork, ...]
            Strongest first.
        """

    async def start_hotspot(self, *, password: str) -> bool:
        """
        Start the setup hotspot.

        Parameters
        ----------
        password : str
            WPA2 password for the hotspot.

        Returns
        -------
        bool
            True when the hotspot started.
        """

    async def stop_hotspot(self) -> None:
        """Stop the setup hotspot and remove its profile."""

    async def join(self, *, request: JoinRequest) -> JoinOutcome:
        """
        Join a network, keeping its profile only when it worked.

        Parameters
        ----------
        request : JoinRequest
            Network name, password and whether it is hidden.

        Returns
        -------
        JoinOutcome
            How the attempt ended.
        """

    async def forget_all(self) -> None:
        """Delete every saved Wi-Fi profile."""

    async def active_wifi(self) -> str | None:
        """
        Find the Wi-Fi connection in use.

        Returns
        -------
        str | None
            Its UUID, or None with none in use.
        """

    async def reconnect(self, *, uuid: str) -> None:
        """
        Bring a saved connection up again.

        Parameters
        ----------
        uuid : str
            The connection's UUID.
        """

    async def hotspot_saved(self) -> bool:
        """
        Say whether a setup hotspot profile exists.

        Returns
        -------
        bool
            True when one does.
        """


class Resets(Protocol):
    """Resets the settings or erases everything."""

    async def reset(self, *, kind: ResetKind) -> None:
        """
        Carry out a reset; a part that fails is logged, not raised.

        Parameters
        ----------
        kind : ResetKind
            The reset asked for.
        """


class DeviceProbe(Protocol):
    """Finds out the device's facts."""

    async def probe(self) -> DeviceFacts:
        """
        Gather the device's facts.

        Returns
        -------
        DeviceFacts
            What was found; a fact that can't be read is None.
        """


class FeedsClient(Protocol):
    """Fetches GTFS-realtime feeds."""

    async def fetch(self, *, feed: str) -> bytes:
        """
        Download one feed.

        Parameters
        ----------
        feed : str
            Feed path, for example 'v1/gtfs/realtime/buses'.

        Returns
        -------
        bytes
            Protobuf body.
        """

    async def close(self) -> None:
        """Release any connections."""


@dataclass(frozen=True)
class PanelRefresh:
    """
    One attempt to refresh the e-paper panel.

    Attributes
    ----------
    full : bool
        True for a full refresh, False for any kind of patch.
    seconds : float
        How long the attempt took.
    ok : bool
        Whether the panel finished it.
    """

    full: bool
    seconds: float
    ok: bool


class Display(Protocol):
    """
    Somewhere frames are shown.

    A panel driver must honour ``full`` and ``turned``; a copy shown
    elsewhere, such as on a phone, may ignore both.
    """

    def show(
        self,
        *,
        frame: NDArray[np.uint8],
        full: bool = False,
        turned: bool = False,
        quick: bool = False,
        asleep: bool = False,
        clear: bool = False,
        clean_after: int = CLEAN_AFTER,
    ) -> None:
        """
        Show a frame.

        Parameters
        ----------
        frame : NDArray[np.uint8]
            480 by 800 grey pixels, drawn the right way up.
        full : bool
            Whether to do a full refresh, clearing ghosting, rather than
            a partial one.
        turned : bool
            Whether to show the frame upside down, for a panel mounted
            the other way round.
        quick : bool
            A knob was turned just now; a panel may trade grey for speed.
        asleep : bool
            The device is in Sleep mode; a panel may deep-sleep after
            drawing.
        clear : bool
            Whether to do a clear refresh: the whole screen in black and
            white, with no wipe. A full refresh wins over it.
        clean_after : int
            Quick patches a panel draws before a cleaning full refresh.
        """

    def settle(self, *, timeout: float) -> bool:
        """
        Wait until every frame shown so far is on the panel.

        Parameters
        ----------
        timeout : float
            Seconds to wait at most.

        Returns
        -------
        bool
            True when it is.
        """


class Clock(Protocol):
    """The current time."""

    def now(self) -> datetime:
        """
        The current Sydney time.

        Returns
        -------
        datetime
            Aware datetime.
        """


class Observer(Protocol):
    """Receives timing events from the running app."""

    def frame_drawn(self, *, render_ms: float) -> None:
        """
        Record that a frame was rendered.

        Parameters
        ----------
        render_ms : float
            Time taken to render, in milliseconds.
        """

    def feed_parsed(
        self, *, feed: str, fetch_ms: float, parse_ms: float, trips: int
    ) -> None:
        """
        Record that a realtime feed was fetched and parsed.

        Parameters
        ----------
        feed : str
            Feed path.
        fetch_ms : float
            Download time in milliseconds.
        parse_ms : float
            Parse time in milliseconds.
        trips : int
            Number of trip updates found.
        """

    def ticked(self) -> None:
        """Record that the app loop ran once."""

    def settings_seen(self, *, settings: Settings) -> None:
        """
        Record the settings in force.

        Parameters
        ----------
        settings : Settings
            The settings, at start and after each change.
        """

    def status_seen(self, *, status: DeviceStatus) -> None:
        """
        Record the device status.

        Parameters
        ----------
        status : DeviceStatus
            The status, at start and after each change.
        """

    def pins_seen(self, *, pins: tuple[Pin, ...], shown: int | None) -> None:
        """
        Record the pins.

        Parameters
        ----------
        pins : tuple[Pin, ...]
            The pins, in knob order, at start and after each change.
        shown : int | None
            Index of the pin on screen, or None when the unpinned place
            is.
        """

    def panel_refreshed(self, *, refresh: PanelRefresh) -> None:
        """
        Record an attempt to refresh the e-paper panel.

        Parameters
        ----------
        refresh : PanelRefresh
            The attempt.
        """


@dataclass(frozen=True)
class Loaded:
    """
    What the state repository found.

    Attributes
    ----------
    saved : Saved
        The saved state, or the defaults.
    stored : bool
        False when there was no file or it fell back to the defaults.
    """

    saved: Saved
    stored: bool


class StateRepository(Protocol):
    """Keeps the pins and view across restarts."""

    def load(self) -> Loaded:
        """
        Read the saved state.

        Returns
        -------
        Loaded
            The saved state, or the defaults when there is none.
        """

    def save(self, *, saved: Saved) -> None:
        """
        Write the saved state.

        Parameters
        ----------
        saved : Saved
            The pins and view.

        Raises
        ------
        OSError
            When it can't be written.
        """


class KeyStore(Protocol):
    """Holds the TfNSW API key."""

    def read(self) -> str | None:
        """
        Read the key.

        Returns
        -------
        str | None
            The key, or None when none is set.
        """

    def present(self) -> bool:
        """
        Say whether a key is set.

        Returns
        -------
        bool
            True when there is a key.
        """

    def write(self, *, key: str) -> None:
        """
        Store a key.

        Parameters
        ----------
        key : str
            The key.

        Raises
        ------
        ValueError
            When it is not a valid key.
        """

    def clear(self) -> None:
        """Remove the key."""


class PanelBus(Protocol):
    """The wires to a UC8179 e-paper controller."""

    def reset(self) -> None:
        """Pulse the reset pin: high 20 ms, low 2 ms, high 20 ms."""

    def command(self, *, code: int, data: bytes = b'') -> None:
        """
        Send a command byte, then its data.

        Parameters
        ----------
        code : int
            The command byte, sent with the data/command pin low.
        data : bytes
            The data bytes, sent with the pin high.
        """

    def idle(self) -> bool:
        """
        Read the busy pin.

        Returns
        -------
        bool
            True when the pin reads 1, which means the panel is idle.
        """

    def pause(self, *, seconds: float) -> None:
        """
        Wait.

        Parameters
        ----------
        seconds : float
            How long to wait.
        """

    def power(self, *, on: bool) -> None:
        """
        Switch the panel's power pin.

        Parameters
        ----------
        on : bool
            True for on.
        """

    def close(self) -> None:
        """Release the pins and the SPI device."""
