"""Tests for the pin editor's checking of the choices made."""

from paperroute.adapters.pin_editor import edited
from paperroute.core.chooser import Choice
from paperroute.core.model import Pin, RouteKind, StopPlace
from paperroute.core.pin_choices import PinDraft
from tests.fake_pins import FakePinOptions

HOME = StopPlace(stop_id='H', name='Home', point=None)
WORK = StopPlace(stop_id='W', name='Work', point=None)
FAR = StopPlace(stop_id='F', name='Far', point=None)
T1 = Choice(key='train:T1', label='Line T1', badge='T1')
T2 = Choice(key='train:T2', label='Line T2', badge='T2')
BUS = Choice(key='bus:809', label='Line 809', badge='809')


def options() -> FakePinOptions:
    """
    Make options with two train lines at Home and a bus route at Work.

    Returns
    -------
    FakePinOptions
        The options.
    """
    return FakePinOptions(
        places={'H': HOME, 'W': WORK, 'F': FAR},
        lines={('H', RouteKind.TRAIN): (T1, T2), ('W', RouteKind.BUS): (BUS,)},
        to={('H', 'train:T1'): (WORK, FAR)},
        only={('H', RouteKind.TRAIN): ('T1', 'T2')},
    )


def test_single_kind_and_line_are_chosen() -> None:
    """The only kind, and the only line of it, need no choosing."""
    found = edited(options=options(), draft=PinDraft(stop='W'))
    assert found.draft.kind is RouteKind.BUS
    assert found.draft.route == '809'
    assert found.pin == Pin(
        WORK, None, RouteKind.BUS, routes=('809',), nickname=None,
    )


def test_unoffered_to_is_dropped() -> None:
    """A To the line does not offer goes, and the filter with it."""
    draft = PinDraft(
        stop='H', kind=RouteKind.TRAIN, route='T1', to='nowhere',
        only=('T2',),
    )
    found = edited(options=options(), draft=draft)
    assert found.draft.to == ''
    assert found.draft.only == ()
    assert found.pin is not None
    assert found.pin.destination is None


def test_unoffered_kind_drops_line_and_to() -> None:
    """A kind the stop does not have clears the line and the To."""
    draft = PinDraft(
        stop='H', kind=RouteKind.FERRY, route='T1', to='W', nickname='Me',
    )
    found = edited(options=options(), draft=draft)
    assert found.draft.route == ''
    assert found.draft.to == ''
    assert found.draft.nickname == 'Me'
    assert found.lines == (T1, T2)
    assert found.pin is None


def test_unoffered_stop_drops_everything() -> None:
    """A stop that is not known clears every choice but the name."""
    draft = PinDraft(
        stop='gone', kind=RouteKind.TRAIN, route='T1', nickname='Me',
        ref='abc',
    )
    found = edited(options=options(), draft=draft)
    assert found.origin is None
    assert found.draft == PinDraft(nickname='Me', ref='abc')
    assert found.kinds == ()


def test_unoffered_line_is_dropped_with_its_to() -> None:
    """A line the kind does not have clears the line and the To."""
    draft = PinDraft(stop='H', kind=RouteKind.TRAIN, route='T9', to='W')
    found = edited(options=options(), draft=draft)
    assert found.draft.route == ''
    assert found.draft.to == ''
    assert found.destinations == ()
    assert found.pin is None


def test_complete_draft_gives_pin() -> None:
    """A full draft makes the pin, with a tidy nickname."""
    draft = PinDraft(
        stop='H', kind=RouteKind.TRAIN, route='T1', to='W', only=('T2',),
        nickname='  Home ',
    )
    found = edited(options=options(), draft=draft)
    assert found.pin == Pin(
        HOME, WORK, RouteKind.TRAIN, routes=('T1',), only=('T2',),
        nickname='Home',
    )
    assert found.destinations == (WORK, FAR)
    assert found.filters == ('T1', 'T2')


def test_no_to_offers_the_line_alone() -> None:
    """Without a To the line is the only filter; route ticks are dropped."""
    draft = PinDraft(stop='H', kind=RouteKind.TRAIN, route='T1', only=('T2',))
    found = edited(options=options(), draft=draft)
    assert found.filters == ()
    assert found.pin == Pin(HOME, kind=RouteKind.TRAIN, routes=('T1',))


def test_nearby_comes_only_from_the_missing_pin() -> None:
    """Nearby stops are listed for a missing pin and no other."""
    fake = options()
    fake.near = (WORK,)
    missing = Pin(HOME, kind=RouteKind.TRAIN, routes=('T1',), missing=True)
    assert edited(options=fake, draft=PinDraft()).nearby == ()
    found = edited(options=fake, draft=PinDraft(), missing=missing)
    assert found.nearby == (WORK,)
