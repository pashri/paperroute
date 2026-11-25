"""Tests for state file: golden and kinds."""

import json
import logging
from dataclasses import replace
from pathlib import Path
from typing import Final

import pytest

from paperroute_device.core.device_settings import DeviceSettings, QuietMode
from paperroute_sydney.city import PEAK_WINDOWS, SYDNEY, SydneyMode
from paperroute_testkit.core.factories import pin_at
from paperroute_transit.adapters.state_file import (
    PinModel,
    pin_from_model,
    pin_to_model,
)
from paperroute_transit.core.model import Pin, StopPlace
from paperroute_transit.core.saved import (
    BoardSettings,
    MapFraming,
    Saved,
    Settings,
    default_saved,
    default_settings,
)
from tests.adapters.board_states import BoardStates
from tests.adapters.state_file.helpers import ROTATED, sample
from tests.adapters.state_layout import STATE_V11, STATE_V11_KINDS, STATE_V12
from tests.golden.scenes import GOLDEN

GOLDEN_STATE: Final[Path] = GOLDEN / 'state.json'


def test_v11_file_saves_as_v13_golden(path: Path) -> None:
    """The v11 fixture loads and saves as the v13 golden's bytes."""
    path.write_bytes(STATE_V11.read_bytes())
    repository = BoardStates(path=path, city=SYDNEY)
    loaded = repository.load()
    assert loaded.stored
    repository.save(saved=loaded.saved)
    assert path.read_bytes() == GOLDEN_STATE.read_bytes()


def test_v12_file_saves_as_v13_golden(path: Path) -> None:
    """The v12 fixture loads and saves as the v13 golden's bytes."""
    path.write_bytes(STATE_V12.read_bytes())
    repository = BoardStates(path=path, city=SYDNEY)
    loaded = repository.load()
    assert loaded.stored
    repository.save(saved=loaded.saved)
    assert path.read_bytes() == GOLDEN_STATE.read_bytes()


def test_unknown_device_key_resets_nothing(
    path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """A stray device setting is dropped quietly; the rest are kept."""
    settings = Settings(
        device=DeviceSettings(
            quiet_mode=QuietMode.APP,
            rotate=True,
            clean_after=60,
            swap_knobs=True,
        ),
        board=BoardSettings(auto_rebuild=False, peak_windows=PEAK_WINDOWS),
    )
    repository = BoardStates(path=path, city=SYDNEY)
    repository.save(saved=replace(sample(), settings=settings))
    data = json.loads(path.read_text())
    data['device']['settings']['auto_rebuild'] = True
    path.write_text(json.dumps(data))
    loaded = repository.load()
    assert (loaded.saved.settings, loaded.stored) == (settings, True)
    assert not caplog.records


def test_unknown_kind_drops_pin(
    path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """A pin of a kind the city has no mode for goes; the rest stay."""
    data = json.loads(STATE_V11_KINDS.read_text())
    data['pins'][3]['kind'] = 'tram'
    path.write_text(json.dumps(data))
    loaded = BoardStates(path=path, city=SYDNEY).load()
    assert loaded.stored
    kinds = [pin.kind for pin in loaded.saved.pins]
    assert kinds == [
        mode for mode in SydneyMode if mode != SydneyMode.LIGHT_RAIL
    ] + [None]
    assert [r.levelno for r in caplog.records] == [logging.WARNING]
    assert not list(path.parent.glob('state.json.bad-*'))


def test_unknown_kind_drops_unpinned_and_before(
    path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """An unpinned or before place of an unknown kind goes; pins stay."""
    data = json.loads(STATE_V11_KINDS.read_text())
    data['unpinned']['kind'] = 'tram'
    data['auto']['before']['place']['kind'] = 'tram'
    path.write_text(json.dumps(data))
    loaded = BoardStates(path=path, city=SYDNEY).load()
    assert loaded.stored
    assert len(loaded.saved.pins) == 9
    assert loaded.saved.unpinned is None
    assert loaded.saved.auto.before is None
    assert loaded.saved.auto.occurrence is not None
    assert len(caplog.records) == 2


@pytest.mark.parametrize(
    ('section', 'expected'),
    [
        ('device', replace(sample(), settings=default_settings(city=SYDNEY))),
        ('board', replace(default_saved(city=SYDNEY), settings=ROTATED)),
    ],
)
@pytest.mark.parametrize('broken', [None, 3], ids=['missing', 'number'])
def test_missing_section_reset(
    *,
    path: Path, section: str, expected: Saved, broken: int | None,
) -> None:
    """A missing or non-object section resets alone; the other is kept."""
    repository = BoardStates(path=path, city=SYDNEY)
    repository.save(saved=replace(sample(), settings=ROTATED))
    data = json.loads(path.read_text())
    del data[section]
    if broken is not None:
        data[section] = broken
    path.write_text(json.dumps(data))
    loaded = repository.load()
    assert (loaded.saved, loaded.stored) == (expected, True)
    assert not list(path.parent.glob('state.json.bad-*'))


def test_too_new_section_moved_aside(path: Path) -> None:
    """A board section newer than the code gives defaults and a .bad- file."""
    repository = BoardStates(path=path, city=SYDNEY)
    repository.save(saved=sample())
    data = json.loads(path.read_text())
    data['board']['schema_version'] = 2
    path.write_text(json.dumps(data))
    loaded = repository.load()
    assert (loaded.saved, loaded.stored) == (default_saved(city=SYDNEY), False)
    assert len(list(path.parent.glob('state.json.bad-*'))) == 1


def test_file_without_map_framing_loads_as_approach(path: Path) -> None:
    """A state file saved before the map framing loads as Approach."""
    states = BoardStates(path=path, city=SYDNEY)
    board = replace(
        sample().settings.board, map_framing=MapFraming.CENTRED,
    )
    states.save(
        saved=replace(
            sample(), settings=replace(sample().settings, board=board),
        ),
    )
    data = json.loads(path.read_text())
    del data['board']['settings']['map_framing']
    path.write_text(json.dumps(data))
    loaded = states.load()
    assert loaded.stored
    assert loaded.saved.settings.board.map_framing is MapFraming.APPROACH


ACROSS: Final[StopPlace] = StopPlace(
    stop_id='across', name='Across', point=(100.0, 40.0),
)


BOTH: Final[Pin] = replace(
    pin_at('home', x=100.0), kind=SydneyMode.BUS, opposite=ACROSS,
    towards=('Town', ''),
)


def test_pin_from_model_loads_a_pin_stored_before_both_sides() -> None:
    """A pin stored before both-sides pins loads as one kerb."""
    plain = pin_at('home', x=100.0)
    stored = pin_to_model(pin=plain).model_dump()
    del stored['opposite'], stored['towards']
    assert pin_from_model(model=PinModel.model_validate(stored)) == plain


def test_pin_round_trips_its_opposite_kerb() -> None:
    """A both-sides pin keeps its kerb across and towards names."""
    assert pin_from_model(model=pin_to_model(pin=BOTH)) == BOTH


def test_state_file_round_trips_a_pin_parent() -> None:
    """A converted pin keeps the parent it came from."""
    converted = replace(BOTH, parent=ACROSS)
    assert pin_from_model(model=pin_to_model(pin=converted)) == converted
    stored = pin_to_model(pin=converted).model_dump()
    del stored['parent']
    assert pin_from_model(model=PinModel.model_validate(stored)) == BOTH


def test_pin_from_model_drops_an_opposite_beside_a_destination() -> None:
    """A stored pin with a Destination and a kerb across loads one kerb."""
    routed = replace(BOTH, destination=ACROSS)
    loaded = pin_from_model(model=pin_to_model(pin=routed))
    assert (loaded.opposite, loaded.towards) == (None, ())
