"""Tests for reading and changing a pin's ways."""

from dataclasses import replace

from multidict import MultiDict

from paperroute.adapters.ways_editor import (
    WaysDraft,
    added_way,
    decode_way,
    draft_pin,
    encode_way,
    never_choices,
    picked_way,
    ways_draft,
    ways_query,
)
from paperroute.core.model import Change, Way
from tests.adapters.test_connection_editor import OPTIONS
from tests.core.connection_factories import (
    CAMPBELLTOWN_WAY,
    CONSTITUTION,
    HAMMERS,
    TWO_WAY,
    WORK,
    WORK_CHANGE,
)


def test_same_stops_same_way() -> None:
    """A way with the same stops replaces the old one's allows and ticks."""
    unticked = replace(WORK, ways=(replace(WORK.ways[0], ticked=False),))
    again = Way(changes=(replace(WORK_CHANGE, allow=9),), ticked=False)
    pin = added_way(pin=unticked, way=again)
    assert pin.ways == (Way(changes=(replace(WORK_CHANGE, allow=9),)),)
    more = added_way(pin=pin, way=CAMPBELLTOWN_WAY)
    assert more.ways == (*pin.ways, CAMPBELLTOWN_WAY)


def test_ways_round_trip() -> None:
    """Ways and their ticks survive the form; broken ones drop alone."""
    pin = replace(
        WORK, ways=(replace(TWO_WAY, ticked=False), CAMPBELLTOWN_WAY),
        never=('665',),
    )
    fields = MultiDict([
        *ways_query(pin=pin, ref='r'), ('ways', 'nowhere,G215254,3'),
    ])
    draft = ways_draft(query=fields)
    assert draft.ref == 'r'
    assert draft.ticked == frozenset({1})
    found = draft_pin(options=OPTIONS, draft=draft, base=WORK)
    assert found == pin
    assert encode_way(way=TWO_WAY) == 'G215254,2145648,7|214510,214510,4'
    for text in ('a', 'G215254,2145648,0', 'G215254,2145648,x',
                 'G215254,2145648,1|G215254,2145648,1|G215254,2145648,1'):
        assert decode_way(options=OPTIONS, text=text, ticked=True) is None


def test_picked_way_allows_from_distance() -> None:
    """A picked way allows the walk at 80 m a minute plus 2."""
    way = picked_way(options=OPTIONS, text='G215254,2145648')
    assert way == Way(changes=(
        Change(alight=HAMMERS, board=CONSTITUTION, allow=4),
    ))
    assert picked_way(options=OPTIONS, text='G215254') is None
    assert picked_way(options=OPTIONS, text='G215254,nowhere') is None
    picked = draft_pin(
        options=OPTIONS,
        draft=WaysDraft(
            origin='G2145615', destination='214510',
            pick='G215254,2145648',
        ),
        base=None,
    )
    assert picked is not None
    assert picked.ways == (way,)
    assert draft_pin(
        options=OPTIONS, draft=WaysDraft(origin='x'), base=None,
    ) is None


def test_never_choices_cover_every_leg() -> None:
    """The never list offers each leg's routes once, then the never ones."""
    pin = replace(WORK, never=('999',))
    assert never_choices(options=OPTIONS, pin=pin) == ('660', '665', '999')
