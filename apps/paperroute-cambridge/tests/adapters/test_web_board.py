"""Tests for Cambridge's board pages."""

from paperroute_cambridge.adapters.web_board import (
    CAMBRIDGE_CREDITS,
    cambridge_status_line,
)


def test_credits_name_the_licence_and_the_map() -> None:
    """Credits name the licence, the train data and the map."""
    assert CAMBRIDGE_CREDITS == (
        'Bus and coach data: contains public sector information licensed '
        'under the <a href="https://www.nationalarchives.gov.uk/doc/'
        'open-government-licence/version/3/">Open Government Licence '
        'v3.0</a>.',
        'Train timetables: Rail Delivery Group and National Rail '
        'Enquiries, through <a href="https://github.com/planarnetwork/'
        'gb-transit">gb-transit</a>. Station data: Department for '
        'Transport.',
        'Map data © <a href="https://www.openstreetmap.org/copyright">'
        'OpenStreetMap</a> contributors.',
    )


def test_status_line_reports_the_positions_parse() -> None:
    """Status line reports the positions parse."""
    status = {
        'frame_age_s': 1.4,
        'last_render_ms': 80.5,
        'feeds': {'bods/positions': {'parse_ms': 12.2}},
    }
    assert cambridge_status_line(status=status) == (
        'Frame age 1 s, render 81 ms, positions parse 12 ms'
    )
