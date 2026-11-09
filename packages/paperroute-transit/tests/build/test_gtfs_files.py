"""Tests for GTFS file reading."""

from datetime import date
from pathlib import Path

import pytest

from paperroute_testkit.build.feed_zip import write_feed
from paperroute_transit.build.gtfs_files import (
    active_dates,
    last_service_day,
    parse_time,
    read_rows,
)

NEVER = {name: '0' for name in (
    'monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday',
    'sunday')}
WEEKDAYS = {'monday': '1', 'tuesday': '1', 'wednesday': '1', 'thursday': '1',
            'friday': '1', 'saturday': '0', 'sunday': '0'}


def test_parse_time_after_midnight() -> None:
    """GTFS times past 24:00 count on from the service day."""
    assert parse_time('25:10:00') == 90600


def test_parse_time_single_digit_hour() -> None:
    """Hours may have one digit and surrounding spaces."""
    assert parse_time(' 5:01:00') == 18060


@pytest.mark.parametrize('value', ['', '5:01', 'xx:00:00'])
def test_parse_time_rejects_bad_values(value: str) -> None:
    """Malformed times raise ValueError."""
    with pytest.raises(ValueError):
        parse_time(value)


def test_read_rows_missing_member_is_empty(tmp_path: Path) -> None:
    """A member that isn't in the zip yields nothing."""
    feed = write_feed(
        path=tmp_path / 'f.zip', files={'stops.txt': [{'stop_id': '1'}]}
    )
    assert not list(read_rows(archive=feed, member='calendar_dates.txt'))


def test_read_rows_strips_bom(tmp_path: Path) -> None:
    """A UTF-8 byte order mark doesn't leak into the first header."""
    feed = tmp_path / 'f.zip'
    write_feed(path=feed, files={'stops.txt': [{'\ufeffstop_id': '1'}]})
    row = next(read_rows(archive=feed, member='stops.txt'))
    assert row == {'stop_id': '1'}


def test_active_dates_weekdays_and_exceptions(tmp_path: Path) -> None:
    """Calendar weekdays apply within range; exceptions add and remove."""
    feed = write_feed(path=tmp_path / 'f.zip', files={
        'calendar.txt': [{'service_id': 'wk', **WEEKDAYS,
                          'start_date': '20261001', 'end_date': '20261031'}],
        'calendar_dates.txt': [
            {'service_id': 'wk', 'date': '20261016', 'exception_type': '2'},
            {'service_id': 'extra', 'date': '20261017', 'exception_type': '1'},
        ],
    })
    dates = active_dates(archive=feed, start=date(2026, 10, 15), days=3)
    assert dates['wk'] == frozenset({date(2026, 10, 15)})
    assert dates['extra'] == frozenset({date(2026, 10, 17)})


def test_active_dates_without_calendar_dates(tmp_path: Path) -> None:
    """Feeds without calendar_dates.txt still work."""
    feed = write_feed(path=tmp_path / 'f.zip', files={
        'calendar.txt': [{'service_id': 'wk', **WEEKDAYS,
                          'start_date': '20261001', 'end_date': '20261031'}]})
    dates = active_dates(archive=feed, start=date(2026, 10, 17), days=2)
    assert dates.get('wk', frozenset()) == frozenset()


def test_last_service_day_takes_the_latest_end_or_added_date(
    tmp_path: Path,
) -> None:
    """The latest running calendar end or added date wins."""
    feed = write_feed(path=tmp_path / 'f.zip', files={
        'calendar.txt': [
            {'service_id': 'wk', **WEEKDAYS,
             'start_date': '20261001', 'end_date': '20261031'},
            {'service_id': 'off', **NEVER,
             'start_date': '20261001', 'end_date': '20261231'},
        ],
        'calendar_dates.txt': [
            {'service_id': 'wk', 'date': '20261105', 'exception_type': '1'},
            {'service_id': 'wk', 'date': '20261201', 'exception_type': '2'},
        ],
    })
    assert last_service_day(archive=feed) == date(2026, 11, 5)


def test_last_service_day_from_calendar_alone(tmp_path: Path) -> None:
    """A feed with only calendar.txt ends on its latest end date."""
    feed = write_feed(path=tmp_path / 'f.zip', files={
        'calendar.txt': [{'service_id': 'wk', **WEEKDAYS,
                          'start_date': '20261001', 'end_date': '20261031'}]})
    assert last_service_day(archive=feed) == date(2026, 10, 31)


def test_last_service_day_without_a_calendar(tmp_path: Path) -> None:
    """A feed with no calendar at all has no last day."""
    feed = write_feed(
        path=tmp_path / 'f.zip', files={'stops.txt': [{'stop_id': '1'}]},
    )
    assert last_service_day(archive=feed) is None
