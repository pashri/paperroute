"""Tests for reading a route's colours."""

import pytest

from paperroute_transit.core.route_colours import RouteColour, colour_of


def test_valid_colour_kept() -> None:
    """A valid pair is kept in lower case."""
    found = colour_of(fill='F99D1C', text='000000')
    assert found == RouteColour(fill='f99d1c', text='000000')


@pytest.mark.parametrize(
    'fill', ['FFF', 'F99D1C;}', '#00B5EF', '', None],
)
def test_bad_colours_are_ignored(fill: str | None) -> None:
    """
    A fill that is not six hex digits gives no colour.

    Parameters
    ----------
    fill : str | None
        The fill.
    """
    assert colour_of(fill=fill, text='000000') is None


@pytest.mark.parametrize('text', [None, 'zz'])
def test_text_colour_contrasts(text: str | None) -> None:
    """
    A missing or bad text colour is black or white, whichever reads.

    Parameters
    ----------
    text : str | None
        The text colour.
    """
    assert colour_of(fill='00B5EF', text=text) == RouteColour(
        fill='00b5ef', text='000000',
    )
    assert colour_of(fill='002664', text=text) == RouteColour(
        fill='002664', text='ffffff',
    )
