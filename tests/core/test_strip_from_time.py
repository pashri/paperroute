"""Tests for the strip's From and Destination rows, which carry no tag."""

from dataclasses import replace

from paperroute.core.strip_view import build_strip_pane, build_strip_view
from paperroute_testkit.core.factories import inputs_of, line_pattern, live
from paperroute_testkit.core.strip_factories import (
    WEST,
    long_reach,
    row_named,
    three_trains,
    times,
)


def test_from_row_shows_its_next_departure() -> None:
    """The From row has no tag and shows the strip's next departure."""
    view = build_strip_view(inputs=three_trains())
    origin = row_named(rows=view.rows, name='C')
    assert (origin.tag, origin.time, origin.bold) == ('', '8:00', True)


def test_from_row_shows_the_next_departure_to_the_destination() -> None:
    """With a Destination the From shows the next train that gets there."""
    view = build_strip_view(
        inputs=replace(three_trains(), destination='W1'),
    )
    origin = row_named(rows=view.rows, name='C')
    target = row_named(rows=view.rows, name='W1')
    assert (origin.tag, origin.time) == ('', '8:03')
    assert (target.tag, target.time, target.bold) == ('', '8:09', True)


def test_from_row_heads_for_a_destination_off_the_strip() -> None:
    """A Destination past the strip's rows still picks the From's train."""
    inputs = three_trains()
    inputs = replace(
        inputs,
        destination='X',
        patterns={
            **inputs.patterns,
            'w': line_pattern(trip_id='w', stations=(*WEST, 'X'), minutes=3),
        },
    )
    view = build_strip_view(inputs=inputs)
    assert row_named(rows=view.rows, name='C').time == '8:03'
    assert all(not row.bold for row in view.rows if row.name != 'C')


def test_from_row_skips_cancelled_departures() -> None:
    """A cancelled first train leaves the From showing the next one."""
    inputs = three_trains()
    first, *rest = inputs.departures
    cancelled = replace(
        inputs,
        departures=(live(item=first.departure, cancelled=True), *rest),
    )
    assert times(inputs=cancelled)['C'] == '8:03'


def test_from_row_without_departures_has_no_time() -> None:
    """A From with nothing leaving shows no time and no tag."""
    view = build_strip_view(inputs=inputs_of(reach=long_reach()))
    origin = row_named(rows=view.rows, name='S20')
    assert (origin.tag, origin.time) == ('', '')


def test_pane_from_row_has_no_tag() -> None:
    """The Strip + map pane drops the FROM tag too."""
    pane = build_strip_pane(inputs=three_trains())
    assert (pane.rows[2].name, pane.rows[2].tag) == ('C', '')
    assert pane.rows[2].time == '8:00'
