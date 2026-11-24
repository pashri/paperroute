"""Replay a recorded slice through the bus delay estimates."""

from collections import defaultdict
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Final

import pytest
from google.transit import gtfs_realtime_pb2

from paperroute_cambridge.adapters.bods_positions import parse_positions
from paperroute_cambridge.area import CAMBRIDGE_PROJECTION
from paperroute_cambridge.cambridge_feeds import ESTIMATES_FEED
from paperroute_cambridge.city import CAMBRIDGE, CAMBRIDGE_REF
from paperroute_transit.adapters.timetable_db import SqliteTimetableRepository
from paperroute_transit.app_effects import estimates_of, loaded_patterns
from paperroute_transit.build.gtfs_files import Source
from paperroute_transit.build.timetable import Window, build_timetable
from paperroute_transit.core.estimates import run_day
from paperroute_transit.core.lateness import Line, line_of, placed
from paperroute_transit.core.messages import Estimating, LoadPatterns
from paperroute_transit.core.model import service_seconds
from paperroute_transit.core.patterns import TripPattern
from paperroute_transit.core.positions import PositionsSnapshot
from paperroute_transit.measures import StopMeasures
from paperroute_transit.ports import TimetableRepository
from tests.conftest import FIXTURES

ESTIMATES: Final[Path] = FIXTURES / 'estimates'
SNAPSHOTS: Final[tuple[Path, ...]] = tuple(sorted(ESTIMATES.glob('*.pb')))
DAY: Final[date] = date(2026, 11, 17)
STALE: Final[timedelta] = timedelta(minutes=3)
HORIZONS: Final[dict[int, int]] = {120: 30, 300: 60, 600: 60}  # seconds
RIGHT: Final[int] = 120  # seconds either side of the pass
LONGEST_GAP: Final[int] = 120  # seconds between two fixes read as a pass
LEAD: Final[float] = 0.15  # share of samples


@dataclass(frozen=True)
class Fix:
    """
    One placed position.

    Attributes
    ----------
    at : int
        Seconds into the service day.
    along : float
        Metres along the trip's line.
    late : int
        Seconds behind the timetable.
    """

    at: int
    along: float
    late: int


@dataclass(frozen=True)
class Replay:
    """
    Every placed fix of a recording.

    Attributes
    ----------
    tracks : Mapping[str, tuple[Fix, ...]]
        Fixes by trip, in time order.
    patterns : Mapping[str, TripPattern]
        The trips' patterns.
    lines : Mapping[str, Line]
        The trips' lines.
    """

    tracks: Mapping[str, tuple[Fix, ...]]
    patterns: Mapping[str, TripPattern]
    lines: Mapping[str, Line]


@dataclass(frozen=True)
class Score:
    """
    How often each way of telling the time was right.

    Attributes
    ----------
    samples : int
        Sampled passes.
    estimated : int
        Passes the estimate got right.
    timetable : int
        Passes the timetable got right.
    """

    samples: int
    estimated: int
    timetable: int


def read_snapshot(*, path: Path) -> PositionsSnapshot:
    """
    Read a recorded download, fetched at its header time.

    Parameters
    ----------
    path : Path
        The recorded file.

    Returns
    -------
    PositionsSnapshot
        Its vehicles.
    """
    data = path.read_bytes()
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(data)
    return parse_positions(
        data=data,
        fetched_at=datetime.fromtimestamp(feed.header.timestamp, tz=UTC),
        projection=CAMBRIDGE_PROJECTION,
    )


def learned(
    *,
    timetable: TimetableRepository,
    measures: StopMeasures,
    snapshot: PositionsSnapshot,
    known: Mapping[str, TripPattern],
) -> tuple[dict[str, TripPattern], dict[str, Line]]:
    """
    Load the patterns and lines of trips not seen before.

    Parameters
    ----------
    timetable : TimetableRepository
        Timetable.
    measures : StopMeasures
        Stop measurements.
    snapshot : PositionsSnapshot
        The download.
    known : Mapping[str, TripPattern]
        Trips already loaded.

    Returns
    -------
    tuple[dict[str, TripPattern], dict[str, Line]]
        New patterns and lines by trip.
    """
    fresh = frozenset(
        vehicle.trip_id
        for vehicle in snapshot.vehicles
        if vehicle.trip_id and vehicle.trip_id not in known
    )
    loaded = loaded_patterns(
        timetable=timetable,
        effect=LoadPatterns(trip_ids=fresh, day=DAY),
        measures=measures,
    )
    patterns = {
        trip: pattern
        for trip, pattern in loaded.patterns.items()
        if pattern is not None
    }
    lines = {
        trip: line
        for trip, pattern in patterns.items()
        if (
            line := line_of(
                pattern=pattern,
                shape=loaded.shapes.get(pattern.shape_id or ''),
            )
        )
    }
    return patterns, lines


def fixes_of(
    *,
    snapshot: PositionsSnapshot,
    patterns: Mapping[str, TripPattern],
    lines: Mapping[str, Line],
    timetable: TimetableRepository,
    day: date,
) -> Iterator[tuple[str, Fix]]:
    """
    Place the fresh vehicles of a download on their trips.

    Parameters
    ----------
    snapshot : PositionsSnapshot
        The download.
    patterns : Mapping[str, TripPattern]
        Patterns by trip.
    lines : Mapping[str, Line]
        Lines by trip.
    timetable : TimetableRepository
        Timetable, for its time zone.
    day : date
        The service day of the recording.

    Yields
    ------
    tuple[str, Fix]
        The trip and the fix.
    """
    zone = timetable.city.zone
    for vehicle in snapshot.vehicles:
        behind = snapshot.header_time - vehicle.timestamp
        trip = vehicle.trip_id
        if behind > STALE.total_seconds() or trip not in lines:
            continue
        found = placed(
            pattern=patterns[trip],
            line=lines[trip],
            vehicle=vehicle,
            day=run_day(vehicle=vehicle, zone=zone),
            zone=zone,
        )
        if found is not None:
            moment = datetime.fromtimestamp(vehicle.timestamp, tz=zone)
            at = service_seconds(day=day, moment=moment, zone=zone)
            yield trip, Fix(at=at, along=found.along, late=found.late)


def replayed(
    *,
    paths: Sequence[Path],
    timetable: TimetableRepository,
    measures: StopMeasures,
    day: date,
) -> Replay:
    """
    Place every recorded fix of the trips in some downloads.

    Parameters
    ----------
    paths : Sequence[Path]
        The recorded files, in time order.
    timetable : TimetableRepository
        Timetable.
    measures : StopMeasures
        Stop measurements.
    day : date
        The service day of the recording.

    Returns
    -------
    Replay
        The fixes with the patterns and lines of their trips.
    """
    patterns: dict[str, TripPattern] = {}
    lines: dict[str, Line] = {}
    tracks: dict[str, list[Fix]] = defaultdict(list)
    for path in paths:
        snapshot = read_snapshot(path=path)
        new_patterns, new_lines = learned(
            timetable=timetable,
            measures=measures,
            snapshot=snapshot,
            known=patterns,
        )
        patterns.update(new_patterns)
        lines.update(new_lines)
        for trip, fix in fixes_of(
            snapshot=snapshot,
            patterns=patterns,
            lines=lines,
            timetable=timetable,
            day=day,
        ):
            if not tracks[trip] or tracks[trip][-1].at != fix.at:
                tracks[trip].append(fix)
    return Replay(
        tracks={trip: tuple(track) for trip, track in tracks.items()},
        patterns=patterns,
        lines=lines,
    )


def passes(
    *, fixes: Sequence[Fix], stops: Sequence[float],
) -> list[tuple[int, float]]:
    """
    Find when a trip reached each stop, from its fixes.

    Parameters
    ----------
    fixes : Sequence[Fix]
        The trip's fixes in time order.
    stops : Sequence[float]
        Metres along the line at each stop.

    Returns
    -------
    list[tuple[int, float]]
        Stop index and the interpolated time of the pass, for each stop
        crossed between two fixes no more than LONGEST_GAP apart.
    """
    found: list[tuple[int, float]] = []
    for index in range(1, len(stops)):
        for one, two in zip(fixes, fixes[1:]):
            if (
                two.at - one.at <= LONGEST_GAP
                and one.along < stops[index] <= two.along
            ):
                share = (stops[index] - one.along) / (two.along - one.along)
                found.append((index, one.at + (two.at - one.at) * share))
                break
    return found


def nearest(
    *, fixes: Sequence[Fix], along: float, at: float, horizon: int,
) -> Fix | None:
    """
    Pick the fix that came about a horizon before a pass.

    Parameters
    ----------
    fixes : Sequence[Fix]
        The trip's fixes in time order.
    along : float
        Metres along the line at the stop.
    at : float
        Seconds into the service day of the pass.
    horizon : int
        Seconds ahead to look from.

    Returns
    -------
    Fix | None
        The fix short of the stop whose lead is nearest the horizon and
        within its allowance; the earlier on a tie.
    """
    short = [fix for fix in fixes if fix.along < along]
    if not short:
        return None
    best = min(short, key=lambda fix: abs(at - fix.at - horizon))
    if abs(at - best.at - horizon) > HORIZONS[horizon]:
        return None
    return best


def scores(*, replay: Replay) -> dict[int, Score]:
    """
    Count how often estimates and the timetable were right.

    Parameters
    ----------
    replay : Replay
        The placed fixes.

    Returns
    -------
    dict[int, Score]
        A score for each horizon.
    """
    counts = {horizon: [0, 0, 0] for horizon in HORIZONS}
    for trip, fixes in replay.tracks.items():
        stops = replay.lines[trip].stops
        calls = replay.patterns[trip].stops
        for index, moment in passes(fixes=fixes, stops=stops):
            for horizon in HORIZONS:
                fix = nearest(
                    fixes=fixes,
                    along=stops[index],
                    at=moment,
                    horizon=horizon,
                )
                if fix is None:
                    continue
                due = calls[index].departs
                counts[horizon][0] += 1
                counts[horizon][1] += abs(due + fix.late - moment) <= RIGHT
                counts[horizon][2] += abs(due - moment) <= RIGHT
    return {
        horizon: Score(samples=n, estimated=e, timetable=t)
        for horizon, (n, e, t) in counts.items()
    }


@pytest.fixture(name='timetable', scope='module')
def timetable_fixture(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[SqliteTimetableRepository]:
    """
    Build the slice's timetable for the recorded day and open it.

    Parameters
    ----------
    tmp_path_factory : pytest.TempPathFactory
        Makes the directory for the database.

    Yields
    ------
    SqliteTimetableRepository
        The repository, closed after the module.
    """
    out = tmp_path_factory.mktemp('estimates') / 'timetable.db'
    build_timetable(
        sources=[Source(name='bods', archive=ESTIMATES / 'estimates.zip')],
        window=Window(start=DAY, days=1),
        out=out,
        city_ref=CAMBRIDGE_REF,
    )
    repository = SqliteTimetableRepository(city=CAMBRIDGE, path=out)
    yield repository
    repository.close()


@pytest.fixture(name='replay', scope='module')
def replay_fixture(timetable: SqliteTimetableRepository) -> Replay:
    """
    Place every recorded fix of the slice's trips.

    Parameters
    ----------
    timetable : SqliteTimetableRepository
        The slice's timetable.

    Returns
    -------
    Replay
        The placed fixes.
    """
    return replayed(
        paths=SNAPSHOTS, timetable=timetable, measures=StopMeasures(), day=DAY,
    )


@pytest.mark.parametrize('horizon', sorted(HORIZONS))
def test_recorded_estimates_beat_the_timetable(
    replay: Replay, horizon: int,
) -> None:
    """2, 5 and 10 minutes ahead, estimates are right far more often."""
    score = scores(replay=replay)[horizon]
    assert score.samples >= 150
    assert score.estimated - score.timetable >= LEAD * score.samples


def test_recorded_estimates_answer_only_the_asked_trips(
    timetable: SqliteTimetableRepository,
) -> None:
    """A real download estimates the asked trips, some of them uncertain."""
    first = read_snapshot(path=SNAPSHOTS[0])
    asked = frozenset(vehicle.trip_id for vehicle in first.vehicles)
    half = frozenset(sorted(asked)[:12])
    found = estimates_of(
        timetable=timetable,
        gauge=timetable,
        measures=StopMeasures(),
        positions=first,
        ask=Estimating(feed=ESTIMATES_FEED, trip_ids=asked, stale_after=STALE),
    )
    assert len(found.trips) >= 20 and set(found.trips) <= asked
    assert any(trip.uncertain for trip in found.trips.values())
    assert not all(trip.uncertain for trip in found.trips.values())
    fewer = estimates_of(
        timetable=timetable,
        gauge=timetable,
        measures=StopMeasures(),
        positions=first,
        ask=Estimating(feed=ESTIMATES_FEED, trip_ids=half, stale_after=STALE),
    )
    assert fewer.trips and set(fewer.trips) <= half
