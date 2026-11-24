"""Tests for app: startup."""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, replace

import pytest

from paperroute_device.core.kit_events import QuitRequested
from paperroute_hardware.core.knob_events import Knob, KnobPressed
from paperroute_sydney.city import SYDNEY, TOWN_HALL
from paperroute_testkit.core.factories import pin_of, place
from paperroute_testkit.core.kit_steps import app_of
from paperroute_testkit.fakes import (
    STOP_POINT,
    EmptyPlacedTimetable,
    MemoryStates,
)
from paperroute_transit.core.model import StopPlace
from paperroute_transit.core.places import shown
from paperroute_transit.core.saved import LiveView, default_saved, seeded
from tests.app.helpers import run_sydney, started
from tests.app_effects.helpers import kit_ports
from tests.app_factories import MAP_PIN, PIN, ports_of


async def test_run_starts_on_town_hall_map_without_state() -> None:
    """With no saved state and no seed, Town Hall shows on the Map."""
    states = MemoryStates()
    kit = await run_sydney(
        ports=ports_of(), kit=kit_ports(states=states), max_events=5,
    )
    state = app_of(kit=kit)
    assert states.saves
    first = replace(states.saves[0], quota=default_saved(city=SYDNEY).quota)
    assert first == default_saved(city=SYDNEY)
    assert state.places.pins == ()
    assert shown(state=state).origin.stop_id == TOWN_HALL.stop_id
    assert state.view is LiveView.MAP


async def test_run_seeds_pin_when_no_state(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A seed with no saved state becomes the one pin, and is saved."""
    states = MemoryStates()
    with caplog.at_level(logging.INFO):
        kit = await run_sydney(
            ports=ports_of(),
            kit=kit_ports(states=states),
            seed=MAP_PIN,
            max_events=5,
        )
    state = app_of(kit=kit)
    origin = place('2145615', name='2145615', point=STOP_POINT)
    assert shown(state=state).origin == origin
    assert state.view is LiveView.STOP
    assert states.saves
    assert states.saves[0].pins[0].origin == origin
    assert (
        'state seeded from the command line or environment' in caplog.text
    )


async def test_run_ignores_seed_when_state_exists(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Saved state wins over a seed, and the seed is logged as ignored."""
    stored = seeded(pin=pin_of('2145615', None), city=SYDNEY)
    with caplog.at_level(logging.INFO):
        kit = await run_sydney(
            ports=ports_of(),
            kit=kit_ports(states=MemoryStates(stored=stored)),
            seed=PIN,
            max_events=5,
        )
    assert app_of(kit=kit).places.pins == stored.pins
    assert 'seed ignored' in caplog.text


async def test_quit_writes_unsaved_state() -> None:
    """A change followed at once by a quit is saved before the run ends."""
    states = MemoryStates(stored=seeded(pin=MAP_PIN, city=SYDNEY))

    def hook(post: Callable[[object], None]) -> None:
        """
        Post a bottom press, then a quit.

        Parameters
        ----------
        post : Callable[[object], None]
            Posts an event.
        """
        post(KnobPressed(knob=Knob.BOTTOM))
        post(QuitRequested())

    async with asyncio.timeout(5):
        kit = await run_sydney(
            ports=ports_of(),
            kit=kit_ports(states=states),
            post_hook=hook,
            tick_every=60.0,
        )
    assert not kit.running
    assert states.stored is not None
    assert states.stored.view is LiveView.MAP


@dataclass
class StoplessTimetable(EmptyPlacedTimetable):
    """A timetable that knows no stops."""

    def stop(self, *, stop_id: str) -> StopPlace:
        """
        Find no stop.

        Parameters
        ----------
        stop_id : str
            Stop ID.

        Returns
        -------
        StopPlace
            Never returns.

        Raises
        ------
        KeyError
            Always.
        """
        raise KeyError(stop_id)


def test_starting_state_keeps_unknown_seed_places_bare(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A seed stop the timetable lacks stays bare, with a warning."""
    with caplog.at_level(logging.WARNING):
        state = started(timetable=StoplessTimetable(), seed=MAP_PIN)
    assert shown(state=state) == MAP_PIN
    assert 'stop 2145615 not in the timetable' in caplog.text
