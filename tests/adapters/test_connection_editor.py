"""Tests for checking a connection pin's choices."""

from dataclasses import replace
from typing import Final

from multidict import MultiDict

from paperroute.adapters.connection_editor import (
    ALLOW_RANGE,
    ConnectionDraft,
    allow_minutes,
    connection_draft,
    connection_draft_of,
    connection_edited,
)
from paperroute.core.chooser import Choice
from paperroute.core.model import RouteKind, Way
from paperroute.core.pin_choices import ChangeStop
from tests.core.connection_factories import (
    CONSTITUTION,
    HAMMERS,
    HOLLIS,
    WESTMEAD,
    WORK,
    WORK_CHANGE,
)
from tests.fake_pins import FakePinOptions

OPTIONS: Final[FakePinOptions] = FakePinOptions(
    places={
        place.stop_id: place
        for place in (HOLLIS, HAMMERS, CONSTITUTION, WESTMEAD)
    },
    lines={
        ('G2145615', RouteKind.BUS): (
            Choice(key='606', label='606', badge='606'),
        ),
    },
    to={('G2145615', '606'): (HAMMERS,)},
    changes=(ChangeStop(place=CONSTITUTION, metres=150),),
    reached=(WESTMEAD,),
    between=('660', '665'),
)
START: Final[ConnectionDraft] = ConnectionDraft(
    stop='G2145615', route='606', nickname='Work',
)
FULL: Final[ConnectionDraft] = replace(
    START, off='G215254', on='2145648', to='214510',
)
WORK_FOUR = replace(
    WORK,
    ways=(Way(changes=(replace(WORK_CHANGE, allow=4),)),),
)


def test_steps_in_order() -> None:
    """Each step offers the next choices, and all of them make a pin."""
    first = connection_edited(options=OPTIONS, draft=START)
    assert first.leg.destinations == (HAMMERS,)
    assert first.leg.draft.kind is RouteKind.BUS
    assert first.changes == ()
    assert first.pin is None
    done = connection_edited(options=OPTIONS, draft=FULL)
    assert done.changes == (ChangeStop(place=CONSTITUTION, metres=150),)
    assert done.walk == 150
    assert done.places == (WESTMEAD,)
    assert done.routes == ('660', '665')
    assert done.pin == WORK_FOUR


def test_allow_defaults_and_resets() -> None:
    """Allow starts from the walk and resets when the stop changes."""
    blank = connection_edited(options=OPTIONS, draft=FULL)
    assert blank.draft.allow == '4'
    assert blank.draft.allow_for == '2145648'
    kept = connection_edited(
        options=OPTIONS, draft=replace(FULL, allow='9', allow_for='2145648'),
    )
    assert kept.draft.allow == '9'
    assert kept.pin is not None
    assert kept.pin.ways[0].changes[0].allow == 9
    reset = connection_edited(
        options=OPTIONS, draft=replace(FULL, allow='9', allow_for='other'),
    )
    assert reset.draft.allow == '4'


def test_allow_outside_range_makes_no_pin() -> None:
    """An allow outside 1-60 keeps the text and makes no pin."""
    for text in ('0', '61', 'soon'):
        found = connection_edited(
            options=OPTIONS,
            draft=replace(FULL, allow=text, allow_for='2145648'),
        )
        assert found.draft.allow == text
        assert found.pin is None
    assert allow_minutes(text=' 60 ') == 60
    assert allow_minutes(text='0') is None
    assert ALLOW_RANGE == 'Allow between 1 and 60 minutes.'


def test_unoffered_choice_drops_later() -> None:
    """An unoffered stop to get on at drops it and every later choice."""
    found = connection_edited(
        options=OPTIONS,
        draft=replace(FULL, on='nowhere', only=('665',)),
    )
    draft = found.draft
    assert (draft.off, draft.on, draft.to, draft.only) == (
        'G215254', '', '', (),
    )
    assert draft.allow == ''
    assert found.pin is None
    gone = connection_edited(
        options=OPTIONS, draft=replace(FULL, off='nowhere'),
    )
    assert (gone.draft.off, gone.draft.on, gone.draft.to) == ('', '', '')
    assert gone.changes == ()


def test_only2_kept_if_offered() -> None:
    """Only routes that reach the To are kept."""
    found = connection_edited(
        options=OPTIONS, draft=replace(FULL, only=('665', '999')),
    )
    assert found.draft.only == ('665',)
    assert found.pin is not None


def test_unknown_stop_starts_over() -> None:
    """A From the timetable does not know keeps only the name and ref."""
    found = connection_edited(
        options=OPTIONS, draft=replace(FULL, stop='nowhere', ref='r'),
    )
    assert found.draft == ConnectionDraft(nickname='Work', ref='r')
    assert found.leg.origin is None
    assert found.pin is None


def test_draft_from_query_and_pin() -> None:
    """A draft reads the form's fields, or a saved pin."""
    query = MultiDict([
        ('stop', 'G2145615'), ('kind', 'bus'), ('route', '606'),
        ('off', 'G215254'), ('on', '2145648'), ('allow', '7'),
        ('allow_for', '2145648'), ('to', '214510'), ('only2', '665'),
        ('only2', '660'), ('nickname', 'Work'), ('ref', 'abc'),
    ])
    draft = connection_draft(query=query)
    assert draft == ConnectionDraft(
        stop='G2145615', kind=RouteKind.BUS, route='606', off='G215254',
        on='2145648', allow='7', allow_for='2145648', to='214510',
        only=('665', '660'), nickname='Work', ref='abc',
    )
    assert connection_draft_of(pin=WORK, ref='abc') == replace(
        draft, only=(), route='',
    )
