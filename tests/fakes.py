"""Hand-written stand-ins for the app's ports."""

import sqlite3
from collections.abc import Collection
from dataclasses import dataclass, field
from datetime import date, datetime

import numpy as np
from numpy.typing import NDArray

from paperroute.core.chooser import ChooserPath, Column
from paperroute.core.device_model import (
    DeviceFacts,
    JoinOutcome,
    JoinRequest,
    WifiNetwork,
    WifiStatus,
)
from paperroute.core.geo import MetreBox, Point, Shape, to_metres
from paperroute.core.messages import PowerAction
from paperroute.core.model import Departure, Mode, Pin, StopPlace
from paperroute.core.patterns import TripPattern
from paperroute.core.reach import ReachInput, StripKey
from paperroute.core.saved import DEFAULT_SAVED, Saved
from paperroute.ports import Loaded

STOP_POINT: Point = to_metres(lon=151.0036, lat=-33.8170)


@dataclass
class FakeNetwork:
    """
    A network that gives fixed answers, or fails every read.

    Attributes
    ----------
    column : Column | None
        The column every path leads to.
    line : tuple[StopPlace, ...] | None
        The stations of every towards row.
    data : ReachInput | None
        The runs through every strip's From.
    error : sqlite3.Error | None
        Raised by every read when set.
    """

    column: Column | None = None
    line: tuple[StopPlace, ...] | None = None
    data: ReachInput | None = None
    error: sqlite3.Error | None = None

    def check(self) -> None:
        """
        Fail when a read error is set.

        Raises
        ------
        sqlite3.Error
            The error set.
        """
        if self.error is not None:
            raise self.error

    def choices(self, *, path: ChooserPath) -> Column | None:
        """
        Give the fixed column.

        Parameters
        ----------
        path : ChooserPath
            Ignored.

        Returns
        -------
        Column | None
            The column.
        """
        self.check()
        return self.column

    def towards_line(self, *, towards: int) -> tuple[StopPlace, ...] | None:
        """
        Give the fixed stations.

        Parameters
        ----------
        towards : int
            Ignored.

        Returns
        -------
        tuple[StopPlace, ...] | None
            The stations.
        """
        self.check()
        return self.line

    def reach_input(
        self, *, key: StripKey, origin: StopPlace,
    ) -> ReachInput | None:
        """
        Give the fixed runs.

        Parameters
        ----------
        key : StripKey
            Ignored.
        origin : StopPlace
            Ignored.

        Returns
        -------
        ReachInput | None
            The runs.
        """
        self.check()
        return self.data


@dataclass
class MemoryStates:
    """
    Saved state kept in memory.

    Attributes
    ----------
    stored : Saved | None
        What was last saved; None when nothing ever was.
    error : OSError | None
        Raised by every save when set.
    saves : list[Saved]
        Every state saved, in order.
    """

    stored: Saved | None = None
    error: OSError | None = None
    saves: list[Saved] = field(default_factory=list)

    def load(self) -> Loaded:
        """
        Give what was stored, or the defaults.

        Returns
        -------
        Loaded
            The stored state, or the defaults not stored.
        """
        if self.stored is None:
            return Loaded(saved=DEFAULT_SAVED, stored=False)
        return Loaded(saved=self.stored, stored=True)

    def save(self, *, saved: Saved) -> None:
        """
        Keep a saved state.

        Parameters
        ----------
        saved : Saved
            The pins and view.

        Raises
        ------
        OSError
            The error set.
        """
        if self.error is not None:
            raise self.error
        self.stored = saved
        self.saves.append(saved)


@dataclass
class FrameRecorder:
    """A display that keeps every frame."""

    frames: list[NDArray[np.uint8]] = field(default_factory=list)

    def show(self, *, frame: NDArray[np.uint8]) -> None:
        """
        Keep a frame.

        Parameters
        ----------
        frame : NDArray[np.uint8]
            Frame.
        """
        self.frames.append(frame)


@dataclass(frozen=True)
class FixedClock:
    """A clock stuck at one moment."""

    at: datetime

    def now(self) -> datetime:
        """
        The fixed time.

        Returns
        -------
        datetime
            Time.
        """
        return self.at


@dataclass
class BrokenTimetable:
    """A timetable whose departures query always fails."""

    calls: int = 0

    def stop(self, *, stop_id: str) -> StopPlace:
        """
        Place any stop with no position, named after its ID.

        Parameters
        ----------
        stop_id : str
            Stop ID.

        Returns
        -------
        StopPlace
            The stop, without a point.
        """
        return StopPlace(stop_id=stop_id, name=stop_id, point=None)

    def match_stop(self, *, place: StopPlace) -> StopPlace | None:
        """
        Find no place.

        Parameters
        ----------
        place : StopPlace
            Place to find.

        Returns
        -------
        StopPlace | None
            Always None.
        """
        return None

    def version(self) -> str | None:
        """
        Name no build.

        Returns
        -------
        str | None
            Always None.
        """
        return None

    def schema_version(self) -> str | None:
        """
        Name no schema.

        Returns
        -------
        str | None
            Always None.
        """
        return None

    def shapes(self, *, shape_ids: Collection[str]) -> dict[str, Shape]:
        """
        Know no shapes.

        Parameters
        ----------
        shape_ids : Collection[str]
            Ignored.

        Returns
        -------
        dict[str, Shape]
            Nothing.
        """
        return {}

    def patterns(
        self, *, trip_ids: Collection[str], around: date
    ) -> dict[str, TripPattern]:
        """
        Know no trips.

        Parameters
        ----------
        trip_ids : Collection[str]
            Ignored.
        around : date
            Ignored.

        Returns
        -------
        dict[str, TripPattern]
            Nothing.
        """
        return {}

    def route_labels(self, *, trip_ids: Collection[str]) -> dict[str, str]:
        """
        Know no routes.

        Parameters
        ----------
        trip_ids : Collection[str]
            Ignored.

        Returns
        -------
        dict[str, str]
            Nothing.
        """
        return {}

    def modes_near(self, *, box: MetreBox) -> frozenset[Mode]:
        """
        Know no shapes.

        Parameters
        ----------
        box : MetreBox
            Ignored.

        Returns
        -------
        frozenset[Mode]
            Nothing.
        """
        return frozenset()

    def departures(
        self, *, pin: Pin, start: datetime, end: datetime
    ) -> list[Departure]:
        """
        Fail like a damaged database file.

        Parameters
        ----------
        pin : Pin
            Ignored.
        start : datetime
            Ignored.
        end : datetime
            Ignored.

        Returns
        -------
        list[Departure]
            Never returns.

        Raises
        ------
        sqlite3.DatabaseError
            Always.
        """
        self.calls += 1
        raise sqlite3.DatabaseError('file is not a database')


@dataclass
class PlacedTimetable(BrokenTimetable):
    """A failing timetable whose stops all sit at the test point."""

    def stop(self, *, stop_id: str) -> StopPlace:
        """
        Place any stop at the test point.

        Parameters
        ----------
        stop_id : str
            Stop ID.

        Returns
        -------
        StopPlace
            The stop, named after its ID.
        """
        return StopPlace(stop_id=stop_id, name=stop_id, point=STOP_POINT)


@dataclass
class EmptyPlacedTimetable(PlacedTimetable):
    """A placed timetable with no departures."""

    def departures(
        self, *, pin: Pin, start: datetime, end: datetime
    ) -> list[Departure]:
        """
        Find nothing.

        Parameters
        ----------
        pin : Pin
            Ignored.
        start : datetime
            Ignored.
        end : datetime
            Ignored.

        Returns
        -------
        list[Departure]
            Nothing.
        """
        return []


@dataclass
class FakeBuilder:
    """
    A timetable builder that answers at once and counts its calls.

    Attributes
    ----------
    result : bool
        What each rebuild returns.
    rebuilds : int
        How many rebuilds were asked for.
    stops : int
        How many times it was stopped.
    """

    result: bool = True
    rebuilds: int = 0
    stops: int = 0

    async def rebuild(self) -> bool:
        """
        Count the rebuild and give the set result.

        Returns
        -------
        bool
            `result`.
        """
        self.rebuilds += 1
        return self.result

    async def stop(self) -> None:
        """Count the stop."""
        self.stops += 1


@dataclass
class FakePower:
    """
    A power control that answers at once and notes the screen when called.

    Attributes
    ----------
    result : bool
        What each action returns.
    display : FrameRecorder | None
        The display whose frame count is noted at each call.
    calls : list[tuple[PowerAction, int]]
        Each action asked for, with how many frames were shown by then.
    """

    result: bool = True
    display: FrameRecorder | None = None
    calls: list[tuple[PowerAction, int]] = field(default_factory=list)

    async def control(self, *, action: PowerAction) -> bool:
        """
        Note the call and give the set result.

        Parameters
        ----------
        action : PowerAction
            The action asked for.

        Returns
        -------
        bool
            `result`.
        """
        shown = 0 if self.display is None else len(self.display.frames)
        self.calls.append((action, shown))
        return self.result


@dataclass
class FakeWifi:
    """
    A Wi-Fi control with a fixed link that joins nothing.

    Attributes
    ----------
    link : WifiStatus
        What `status` reports.
    """

    link: WifiStatus = field(default_factory=WifiStatus)

    async def status(self) -> WifiStatus:
        """
        Give the fixed link.

        Returns
        -------
        WifiStatus
            `link`.
        """
        return self.link

    async def scan(self) -> tuple[WifiNetwork, ...]:
        """
        Find nothing.

        Returns
        -------
        tuple[WifiNetwork, ...]
            Empty.
        """
        return ()

    async def start_hotspot(self, *, password: str) -> bool:
        """
        Refuse.

        Parameters
        ----------
        password : str
            Ignored.

        Returns
        -------
        bool
            False.
        """
        return False

    async def stop_hotspot(self) -> None:
        """Do nothing."""

    async def join(self, *, request: JoinRequest) -> JoinOutcome:
        """
        Fail to join.

        Parameters
        ----------
        request : JoinRequest
            Ignored.

        Returns
        -------
        JoinOutcome
            FAILED.
        """
        return JoinOutcome.FAILED

    async def forget_all(self) -> None:
        """Do nothing."""


def device_facts(*, key_set: bool = True) -> DeviceFacts:
    """
    Make facts for a device on no network.

    Parameters
    ----------
    key_set : bool
        Whether a key is set.

    Returns
    -------
    DeviceFacts
        The facts.
    """
    return DeviceFacts(
        wifi=WifiStatus(),
        clock_synced=True,
        uptime_s=60.0,
        cpu_c=45.0,
        disk_free=1_000_000,
        mem_free=2_000_000,
        version='dev',
        last_error=None,
        tiles_built=None,
        key_set=key_set,
    )


@dataclass
class FakeProbe:
    """
    A device probe that gives fixed facts, or raises.

    Attributes
    ----------
    facts : DeviceFacts
        What `probe` gives.
    error : Exception | None
        Raised by `probe` when set.
    """

    facts: DeviceFacts = field(default_factory=device_facts)
    error: Exception | None = None

    async def probe(self) -> DeviceFacts:
        """
        Give the fixed facts.

        Returns
        -------
        DeviceFacts
            `facts`.

        Raises
        ------
        Exception
            `error`, when set.
        """
        if self.error is not None:
            raise self.error
        return self.facts


@dataclass
class FakeKeys:
    """
    A key store holding a key or not.

    Attributes
    ----------
    key : str | None
        The key held.
    """

    key: str | None = field(default=None, repr=False)

    def read(self) -> str | None:
        """
        Give the key.

        Returns
        -------
        str | None
            `key`.
        """
        return self.key

    def present(self) -> bool:
        """
        Say whether a key is held.

        Returns
        -------
        bool
            True when `key` is set.
        """
        return self.key is not None

    def write(self, *, key: str) -> None:
        """
        Hold a key.

        Parameters
        ----------
        key : str
            The key.
        """
        self.key = key

    def clear(self) -> None:
        """Drop the key."""
        self.key = None
