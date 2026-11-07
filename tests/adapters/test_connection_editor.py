"""Tests for checking the choices of a way built by hand."""

from dataclasses import replace
from typing import Final

from multidict import MultiDict

from paperroute.adapters.connection_editor import (
    ALLOW_RANGE,
    ChangeDraft,
    ConnectionDraft,
    allow_minutes,
    connection_draft,
    connection_edited,
)
from paperroute.core.pin_choices import ChangeStop
from paperroute_testkit.core.connection_factories import (
    CAMPBELLTOWN,
    CONSTITUTION,
    HAMMERS,
    HOLLIS,
    PARRAMATTA,
    WESTMEAD,
    WORK_CHANGE,
)
from paperroute_testkit.fake_pins import FakePinOptions
from paperroute_transit.core.model import Change, Way

OPTIONS: Final[FakePinOptions] = FakePinOptions(
    places={
        place.stop_id: place
        for place in (
            HOLLIS, HAMMERS, CONSTITUTION, WESTMEAD, PARRAMATTA,
            CAMPBELLTOWN,
        )
    },
    changes=(ChangeStop(place=CONSTITUTION, metres=150),),
    reached=(WESTMEAD,),
    reached_by={'G2145615': (HAMMERS,)},
    between=('660', '665'),
)
START: Final[ConnectionDraft] = ConnectionDraft(
    stop='G2145615', to='214510',
)
FULL: Final[ConnectionDraft] = replace(
    START, first=ChangeDraft(off='G215254', on='2145648'),
)
WORK_WAY: Final[Way] = Way(changes=(replace(WORK_CHANGE, allow=4),))


def test_steps_in_order() -> None:
    """Each step offers the next choices, and all of them make a way."""
    first = connection_edited(options=OPTIONS, draft=START)
    assert first.origin == HOLLIS
    assert first.target == WESTMEAD
    assert first.firsts == (HAMMERS,)
    assert first.first.stops == ()
    assert first.way is None
    done = connection_edited(options=OPTIONS, draft=FULL)
    assert done.first.stops == (ChangeStop(place=CONSTITUTION, metres=150),)
    assert done.places == (WESTMEAD,)
    assert done.way == WORK_WAY


def test_hand_way_two_changes() -> None:
    """A second change adds its own stops and allowance."""
    two = replace(
        FULL, changes=2,
        second=ChangeDraft(off='214510', on='2145648', allow='3'),
        to='214510',
    )
    found = connection_edited(options=OPTIONS, draft=two)
    assert found.offs == (WESTMEAD,)
    assert found.way == Way(changes=(
        replace(WORK_CHANGE, allow=4),
        Change(alight=WESTMEAD, board=CONSTITUTION, allow=4),
    ))
    kept = connection_edited(
        options=OPTIONS,
        draft=replace(two, second=replace(two.second, allow_for='2145648')),
    )
    assert kept.way is not None
    assert kept.way.changes[1].allow == 3
    unfinished = connection_edited(
        options=OPTIONS, draft=replace(two, second=ChangeDraft()),
    )
    assert unfinished.way is None
    assert unfinished.draft.to == ''


def test_allow_defaults_and_resets() -> None:
    """Allow starts from the walk and resets when the stop changes."""
    blank = connection_edited(options=OPTIONS, draft=FULL)
    assert blank.draft.first.allow == '4'
    assert blank.draft.first.allow_for == '2145648'
    kept = connection_edited(options=OPTIONS, draft=replace(
        FULL, first=replace(FULL.first, allow='9', allow_for='2145648'),
    ))
    assert kept.draft.first.allow == '9'
    assert kept.way is not None
    assert kept.way.changes[0].allow == 9
    reset = connection_edited(options=OPTIONS, draft=replace(
        FULL, first=replace(FULL.first, allow='9', allow_for='other'),
    ))
    assert reset.draft.first.allow == '4'


def test_allow_outside_range_makes_no_way() -> None:
    """An allow outside 1-60 keeps the text and makes no way."""
    for text in ('0', '61', 'soon'):
        found = connection_edited(options=OPTIONS, draft=replace(
            FULL, first=replace(FULL.first, allow=text, allow_for='2145648'),
        ))
        assert found.draft.first.allow == text
        assert found.way is None
    assert allow_minutes(text=' 60 ') == 60
    assert allow_minutes(text='0') is None
    assert ALLOW_RANGE == 'Allow between 1 and 60 minutes.'


def test_unoffered_choice_drops_later() -> None:
    """An unoffered stop to get on at drops it and every later choice."""
    found = connection_edited(options=OPTIONS, draft=replace(
        FULL, first=replace(FULL.first, on='nowhere'),
    ))
    draft = found.draft
    assert (draft.first.off, draft.first.on, draft.to) == ('G215254', '', '')
    assert found.way is None
    gone = connection_edited(options=OPTIONS, draft=replace(
        FULL, first=ChangeDraft(off='nowhere'),
    ))
    assert (gone.draft.first.off, gone.draft.to) == ('', '')
    assert gone.first.stops == ()


def test_unreached_to_makes_no_way() -> None:
    """A To the last ride does not reach is kept as the target, unchecked."""
    found = connection_edited(options=OPTIONS, draft=replace(
        FULL, to='G215254',
    ))
    assert found.target == HAMMERS
    assert found.draft.to == ''
    assert found.way is None


def test_unknown_stop_starts_over() -> None:
    """A From the timetable does not know keeps only where to go back."""
    found = connection_edited(
        options=OPTIONS, draft=replace(FULL, stop='nowhere', back='x=1'),
    )
    assert found.draft == ConnectionDraft(back='x=1', to='214510')
    assert found.origin is None
    assert found.way is None


def test_draft_from_query() -> None:
    """A draft reads the form's fields; `more` asks for one change more."""
    query = MultiDict([
        ('stop', 'G2145615'), ('off', 'G215254'), ('on', '2145648'), ('allow', '7'),
        ('allow_for', '2145648'), ('off2', 'a'), ('on2', 'b'),
        ('allow2', '3'), ('allow2_for', 'b'), ('to', '214510'),
        ('changes', '1'), ('more', '1'), ('back', 'from=G'),
    ])
    assert connection_draft(query=query) == ConnectionDraft(
        stop='G2145615',
        first=ChangeDraft(
            off='G215254', on='2145648', allow='7', allow_for='2145648',
        ),
        second=ChangeDraft(off='a', on='b', allow='3', allow_for='b'),
        changes=2, to='214510', back='from=G',
    )
    odd = connection_draft(query=MultiDict([('changes', 'x')]))
    assert odd.changes == 1
