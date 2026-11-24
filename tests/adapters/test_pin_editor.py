"""Tests for the pin editor's checking of the choices made."""

from dataclasses import dataclass, replace
from typing import Final

from paperroute_sydney.city import SYDNEY, SydneyMode
from paperroute_testkit.fake_pins import FakePinOptions
from paperroute_transit.adapters.pin_editor import edited
from paperroute_transit.core.chooser import Choice
from paperroute_transit.core.model import Pin, StopPlace
from paperroute_transit.core.modes import ModeSpec
from paperroute_transit.core.pin_choices import PinDraft

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
        lines={
            ('H', SydneyMode.TRAIN): (T1, T2),
            ('W', SydneyMode.BUS): (BUS,),
        },
        to={('H', 'train:T1'): (WORK, FAR)},
        only={('H', SydneyMode.TRAIN): ('T1', 'T2')},
    )


def test_single_kind_and_line_are_chosen() -> None:
    """The only kind, and the only line of it, need no choosing."""
    found = edited(options=options(), draft=PinDraft(stop='W'))
    assert found.draft.kind == SydneyMode.BUS
    assert found.draft.route == '809'
    assert found.pin == Pin(
        WORK, None, SydneyMode.BUS, routes=('809',), nickname=None,
    )


def test_unoffered_to_is_dropped() -> None:
    """A To the line does not offer goes; the filter stays."""
    draft = PinDraft(
        stop='H', kind=SydneyMode.TRAIN, route='T1', to='nowhere',
        only=('T2',),
    )
    found = edited(options=options(), draft=draft)
    assert found.draft.to == ''
    assert found.draft.only == ('T2',)
    assert found.pin is not None
    assert found.pin.destination is None


def test_unoffered_kind_drops_line_and_to() -> None:
    """A kind the stop does not have clears the line and the To."""
    draft = PinDraft(
        stop='H', kind=SydneyMode.FERRY, route='T1', to='W', nickname='Me',
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
        stop='gone', kind=SydneyMode.TRAIN, route='T1', nickname='Me',
        ref='abc',
    )
    found = edited(options=options(), draft=draft)
    assert found.origin is None
    assert found.draft == PinDraft(nickname='Me', ref='abc')
    assert not found.kinds


def test_unoffered_line_is_dropped_with_its_to() -> None:
    """A line the kind does not have clears the line and the To."""
    draft = PinDraft(stop='H', kind=SydneyMode.TRAIN, route='T9', to='W')
    found = edited(options=options(), draft=draft)
    assert found.draft.route == ''
    assert found.draft.to == ''
    assert not found.destinations
    assert found.pin is None


def test_complete_draft_gives_pin() -> None:
    """A full draft makes the pin, with a tidy nickname."""
    draft = PinDraft(
        stop='H', kind=SydneyMode.TRAIN, route='T1', to='W', only=('T2',),
        nickname='  Home ',
    )
    found = edited(options=options(), draft=draft)
    assert found.pin == Pin(
        HOME, WORK, SydneyMode.TRAIN, routes=('T1',), only=('T2',),
        nickname='Home',
    )
    assert found.destinations == (WORK, FAR)
    assert found.filters == ('T1', 'T2')


def test_nearby_comes_only_from_the_missing_pin() -> None:
    """Nearby stops are listed for a missing pin and no other."""
    fake = options()
    fake.near = (WORK,)
    missing = Pin(HOME, kind=SydneyMode.TRAIN, routes=('T1',), missing=True)
    assert not edited(options=fake, draft=PinDraft()).nearby
    found = edited(options=fake, draft=PinDraft(), missing=missing)
    assert found.nearby == (WORK,)


def test_line_step_matches_on_the_value() -> None:
    """Two lines of one badge are told apart by their values."""
    norwich = Choice(
        key='train:GA:Norwich', label='Norwich', badge='GA',
        value='GA:Norwich',
    )
    liverpool = Choice(
        key='train:GA:Liverpool Street', label='Liverpool Street',
        badge='GA', value='GA:Liverpool Street',
    )
    found = edited(
        options=FakePinOptions(
            places={'S': StopPlace(stop_id='S', name='Sta', point=None)},
            lines={('S', SydneyMode.TRAIN): (norwich, liverpool)},
        ),
        draft=PinDraft(stop='S', kind=SydneyMode.TRAIN, route='GA:Norwich'),
    )
    assert found.draft.route == 'GA:Norwich'
    assert found.pin is not None
    assert found.pin.routes == ('GA:Norwich',)


D1 = StopPlace(stop_id='D1', name='Dest one', point=None)
D2 = StopPlace(stop_id='D2', name='Dest two', point=None)
BRIGHTON = Choice(
    key='train:TL:B', label='Brighton', badge='TL', value='TL:B',
)
LIVERPOOL = Choice(
    key='train:GA:L', label='Liverpool Street', badge='GA', value='GA:L',
)
KINGS_LYNN = Choice(
    key='train:GN:K', label='Kings Lynn', badge='GN', value='GN:K',
)
ROUTED: Final[tuple[ModeSpec, ...]] = tuple(
    replace(spec, end_points=True)
    if spec.id == SydneyMode.TRAIN
    else spec
    for spec in SYDNEY.modes
)


@dataclass
class KindedOptions(FakePinOptions):
    """Options that reach a place only for a train."""

    def reached_from(
        self, *, place: StopPlace, kind: str | None = None,
    ) -> tuple[StopPlace, ...]:
        """
        Give the places reached by a train, and none for any other kind.

        Parameters
        ----------
        place : StopPlace
            Where the ride starts.
        kind : str | None
            The kind counted.

        Returns
        -------
        tuple[StopPlace, ...]
            The fixed places for a train; otherwise none.
        """
        if kind != SydneyMode.TRAIN:
            return ()
        return super().reached_from(place=place, kind=kind)


def routed_options() -> KindedOptions:
    """
    Make options with three train lines at a station and two places.

    Returns
    -------
    KindedOptions
        The options.
    """
    return KindedOptions(
        places={'S': StopPlace(stop_id='S', name='Sta', point=None)},
        lines={('S', SydneyMode.TRAIN): (BRIGHTON, LIVERPOOL, KINGS_LYNN)},
        reached_by={'S': (D1, D2)},
        only={('S', SydneyMode.TRAIN): ('X1', 'X2')},
    )


OPTIONS: Final[KindedOptions] = routed_options()


def test_a_train_pin_needs_no_line() -> None:
    """A From and a To make a train pin that has no routes."""
    editor = edited(
        options=OPTIONS, draft=PinDraft(stop='S', kind='train', to='D1'),
        modes=ROUTED,
    )
    assert not editor.lines
    assert editor.draft.route == ''
    assert editor.pin is not None
    assert (editor.pin.destination, editor.pin.routes) == (D1, ())


def test_a_train_pin_needs_no_destination() -> None:
    """A From alone makes a train pin."""
    editor = edited(
        options=OPTIONS, draft=PinDraft(stop='S', kind='train'),
        modes=ROUTED,
    )
    assert editor.pin is not None
    assert (editor.pin.destination, editor.pin.routes) == (None, ())


def test_the_train_destinations_are_every_reached_station() -> None:
    """The To offers every place a train reaches, by the From's reach."""
    editor = edited(
        options=OPTIONS, draft=PinDraft(stop='S', kind='train'),
        modes=ROUTED,
    )
    assert editor.destinations == OPTIONS.reached_from(
        place=editor.origin or D1, kind='train',
    )
    assert [p.stop_id for p in editor.destinations] == ['D1', 'D2']


def test_a_stored_train_line_is_ignored() -> None:
    """A route left on an old train pin neither shows nor saves."""
    draft = PinDraft(stop='S', kind='train', to='D1', route='GA:L')
    editor = edited(options=OPTIONS, draft=draft, modes=ROUTED)
    assert editor.draft.route == ''
    assert editor.pin is not None
    assert editor.pin.routes == ()


def test_without_modes_the_line_comes_first() -> None:
    """With no end-point mode, no line is chosen for the owner."""
    draft = PinDraft(stop='S', kind='train', to='D1')
    editor = edited(options=OPTIONS, draft=draft)
    assert editor.draft.route == ''
    assert editor.pin is None
    assert not editor.destinations


def test_a_bus_pin_still_needs_its_route() -> None:
    """A kind that is not an end-point kind waits for its line."""
    other = Choice(key='bus:810', label='Line 810', badge='810')
    found = edited(
        options=FakePinOptions(
            places={'H': HOME}, lines={('H', SydneyMode.BUS): (BUS, other)},
        ),
        draft=PinDraft(stop='H', kind=SydneyMode.BUS),
        modes=ROUTED,
    )
    assert found.pin is None
    assert found.lines == (BUS, other)


def test_the_route_filter_survives_an_end_points_train() -> None:
    """The routes the owner ticked are kept, and the offered ones listed."""
    draft = PinDraft(stop='S', kind='train', to='D1', only=('X2', 'Z'))
    editor = edited(options=OPTIONS, draft=draft, modes=ROUTED)
    assert editor.filters == ('X1', 'X2')
    assert editor.draft.only == ('X2',)


def test_an_unknown_destination_is_dropped_not_chosen() -> None:
    """A lone reachable place is not chosen for the owner."""
    lone = replace(routed_options(), reached_by={'S': (D1,)})
    draft = PinDraft(stop='S', kind='train', to='nowhere')
    editor = edited(options=lone, draft=draft, modes=ROUTED)
    assert editor.draft.to == ''


def test_a_kind_switch_discards_the_old_kinds_choices() -> None:
    """Choices made under a kind the stop lacks are dropped."""
    draft = PinDraft(
        stop='S', kind='ferry', to='D1', only=('X1',), nickname='Me',
        ref='r',
    )
    editor = edited(options=OPTIONS, draft=draft, modes=ROUTED)
    assert (editor.draft.to, editor.draft.only) == ('', ())
    assert (editor.draft.nickname, editor.draft.ref) == ('Me', 'r')
