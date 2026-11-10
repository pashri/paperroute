"""The kit's state file with the board's steps keeps every stored value."""

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Final

from paperroute_device.adapters.state_file import Loaded, StateFile
from paperroute_device.core.app import App, MigrationStep, SectionCodec
from paperroute_device.core.device_state import Banner, DeviceState, KitSaved
from paperroute_device.core.kit_state import Step
from paperroute_device.ports import Frame
from paperroute_hardware.core.knob_events import KnobEvent
from paperroute_sydney.city import SYDNEY
from paperroute_transit.adapters.state_migrations import transit_migrations
from tests.adapters.board_states import BoardStates
from tests.adapters.state_layout import STATE_V12
from tests.golden.scenes import GOLDEN

STATE: Final[Path] = GOLDEN / 'state.json'


class PassCodec:
    """Hands the board section over as it is stored."""

    def load(self, *, data: object) -> object:
        """
        Read the section.

        Parameters
        ----------
        data : object
            The stored section.

        Returns
        -------
        object
            The section unchanged.
        """
        return data

    def dump(self, *, saved: object) -> dict[str, object]:
        """
        Write the section.

        Parameters
        ----------
        saved : object
            The section as read.

        Returns
        -------
        dict[str, object]
            The section unchanged.
        """
        assert isinstance(saved, dict)
        return saved


class BoardSteps(App[object]):
    """An app that registers the board's steps, bound to Sydney."""

    def section(self) -> SectionCodec | None:
        """
        Give a codec that keeps the board section as stored.

        Returns
        -------
        SectionCodec | None
            The codec.
        """
        return PassCodec()

    def migrations(self) -> Mapping[int, MigrationStep]:
        """
        Give the board's steps.

        Returns
        -------
        Mapping[int, MigrationStep]
            Each step with Sydney bound.
        """
        return transit_migrations(city=SYDNEY)

    def start(
        self, *, section: object | None, stored: bool, device: DeviceState,
    ) -> object:
        """
        Keep the section as the state.

        Parameters
        ----------
        section : object | None
            The section.
        stored : bool
            Whether it was stored.
        device : DeviceState
            The device state.

        Returns
        -------
        object
            The section.
        """
        del stored, device
        return section

    # pylint: disable-next=useless-return
    def render(
        self, *, state: object, device: DeviceState, banner: Banner | None,
    ) -> object | None:
        """
        Show nothing.

        Parameters
        ----------
        state : object
            The state.
        device : DeviceState
            The device state.
        banner : Banner | None
            The banner.

        Returns
        -------
        object | None
            Always None.
        """
        del state, device, banner
        return None

    def draw(self, *, view: object) -> Frame:
        """
        Refuse to draw.

        Parameters
        ----------
        view : object
            The view.

        Returns
        -------
        Frame
            Never.

        Raises
        ------
        NotImplementedError
            Always.
        """
        raise NotImplementedError(view)

    def on_knob(
        self, *, state: object, device: DeviceState, event: KnobEvent,
    ) -> Step[object]:
        """
        Ignore a knob.

        Parameters
        ----------
        state : object
            The state.
        device : DeviceState
            The device state.
        event : KnobEvent
            The event.

        Returns
        -------
        Step[object]
            The state unchanged.
        """
        del device, event
        return state, ()


def test_golden_file_round_trips_through_the_kit(tmp_path: Path) -> None:
    """The v13 golden loads with every value and saves back byte for byte."""
    path = tmp_path / 'state.json'
    text = STATE.read_text(encoding='utf-8')
    path.write_text(text, encoding='utf-8')
    states = StateFile(path=path, app=BoardSteps())
    loaded = states.load()
    board_path = tmp_path / 'board.json'
    board_path.write_text(text, encoding='utf-8')
    saved = BoardStates(path=board_path, city=SYDNEY).load().saved
    assert loaded == Loaded(
        device=saved.settings.device,
        app=json.loads(text)['board'],
        stored=True,
    )
    states.save(saved=KitSaved(device=loaded.device, app=loaded.app))
    assert path.read_text(encoding='utf-8') == text


def test_v12_file_reaches_13_as_the_board_migrates_it(tmp_path: Path) -> None:
    """A v12 file reaches 13 through the kit's chain as it does today."""
    kit_path = tmp_path / 'kit.json'
    board_path = tmp_path / 'board.json'
    for path in (kit_path, board_path):
        path.write_bytes(STATE_V12.read_bytes())
    states = StateFile(path=kit_path, app=BoardSteps())
    loaded = states.load()
    states.save(saved=KitSaved(device=loaded.device, app=loaded.app))
    board = BoardStates(path=board_path, city=SYDNEY)
    board.save(saved=board.load().saved)
    assert json.loads(kit_path.read_text()) == json.loads(
        board_path.read_text(),
    )
