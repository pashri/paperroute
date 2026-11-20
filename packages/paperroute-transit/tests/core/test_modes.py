"""Tests for a city's declared modes."""

from dataclasses import replace

from paperroute_transit.core.modes import (
    ModeSpec,
    line_badge,
    line_key,
    line_modes,
    line_value,
    mode_problems,
    named_line_modes,
    spec_of,
    tagged_modes,
)

BUS = ModeSpec(id='bus', label='Bus', letter='B', chip='#00b5ef', tagged=True)
TRAIN = ModeSpec(
    id='train', label='Train', letter='T', chip='#f99d1c', by_line=True,
)


def test_mode_problems_found() -> None:
    """A repeated id, an undeclared parent and a long letter each count."""
    modes = [
        BUS,
        ModeSpec(id='bus', label='Bus again', letter='C', chip='#000000'),
        ModeSpec(
            id='night', label='Night', letter='BB', chip='#000000',
            under='tram',
        ),
    ]
    assert len(mode_problems(modes=modes)) == 3


def test_mode_problems_names_undeclared_links() -> None:
    """Replaces and board_with must name declared modes too."""
    modes = [
        ModeSpec(
            id='rail', label='Rail', letter='R', chip='#000000',
            replaces='tram', board_with=('bus', 'ferry'),
        ),
        BUS,
    ]
    assert mode_problems(modes=modes) == [
        "mode 'rail' replaces names undeclared 'tram'",
        "mode 'rail' board_with names undeclared 'ferry'",
    ]


def test_sound_modes_have_no_problems() -> None:
    """Modes linking only to declared ids are sound."""
    replacement = ModeSpec(
        id='replacement', label='Replacement', letter='R', chip='#00b5ef',
        under='bus', replaces='train',
    )
    assert not mode_problems(modes=[BUS, TRAIN, replacement])


def test_spec_of_finds_by_id() -> None:
    """A spec is found by id; None and unknown ids find nothing."""
    assert spec_of(modes=[BUS, TRAIN], mode='train') is TRAIN
    assert spec_of(modes=[BUS, TRAIN], mode=None) is None
    assert spec_of(modes=[BUS, TRAIN], mode='ferry') is None


def test_line_and_tagged_modes_come_from_flags() -> None:
    """Line modes are the by-line specs; tagged modes the tagged ones."""
    assert line_modes(modes=[BUS, TRAIN]) == frozenset({'train'})
    assert tagged_modes(modes=[BUS, TRAIN]) == frozenset({'bus'})


def test_css_unsafe_values_are_problems() -> None:
    """An id or chip colour unsafe to write into CSS is a problem each."""
    upper = ModeSpec(id='Bus', label='Bus', letter='B', chip='#00b5ef')
    loose = ModeSpec(id='bus', label='Bus', letter='B', chip='#00B5EF;')
    assert len(mode_problems(modes=[upper])) == 1
    assert len(mode_problems(modes=[loose])) == 1


def test_line_badge_splits_at_the_first_colon() -> None:
    """The badge stops at the first colon, however many follow."""
    assert line_badge(value='GA:Norwich:x') == 'GA'


def test_named_line_modes() -> None:
    """Only a by-line spec with named_lines set is listed."""
    modes = [BUS, replace(TRAIN, named_lines=True)]
    assert named_line_modes(modes=modes) == frozenset({TRAIN.id})


def test_a_named_line_key_carries_its_long_name() -> None:
    """A named kind keys by number and long name; others by number."""
    named = frozenset({'train'})
    assert line_key(
        kind='train', number='GA', long_name='Norwich', named=named,
    ) == 'train:GA:Norwich'
    assert line_key(
        kind='metro', number='M1', long_name='North West', named=named,
    ) == 'metro:M1'


def test_line_value_and_badge() -> None:
    """The value drops the kind; the badge is the value's number."""
    value = line_value(key='train:GN:Kings Lynn & Kings Cross', kind='train')
    assert value == 'GN:Kings Lynn & Kings Cross'
    assert line_badge(value=value) == 'GN'
    assert line_badge(value='T1') == 'T1'
