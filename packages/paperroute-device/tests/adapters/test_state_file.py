"""Tests for the kit's state file."""

import json
import logging
from collections.abc import Mapping
from datetime import time
from pathlib import Path
from typing import Final

import pytest

from paperroute_device.adapters.state_file import (
    BAD_KEPT,
    BAD_PREFIX,
    TEMP_PREFIX,
    Loaded,
    StateFile,
)
from paperroute_device.core.app import App, MigrationStep, SectionCodec
from paperroute_device.core.device_settings import (
    DeviceSettings,
    QuietMode,
    Window,
)
from paperroute_device.core.device_state import Banner, DeviceState, KitSaved
from paperroute_device.core.kit_state import Step
from paperroute_device.ports import Frame
from paperroute_device_testkit.paths import ROOT
from paperroute_hardware.core.knob_events import KnobEvent

GOLDEN: Final[Path] = ROOT / 'tests' / 'golden' / 'state.json'
SETTINGS: Final[dict[str, object]] = {
    'quiet_mode': 'app',
    'my_hours': {'start': '22:15', 'end': '06:00'},
    'rotate': True,
    'larger_text': False,
    'inverted_lists': False,
    'clean_after': 60,
    'swap_knobs': False,
}
KEPT: Final[DeviceSettings] = DeviceSettings(
    quiet_mode=QuietMode.APP,
    my_hours=Window(start=time(22, 15), end=time(6)),
    rotate=True,
    clean_after=60,
)


def same(data: dict[str, object]) -> dict[str, object]:
    """
    Leave data as it is.

    Parameters
    ----------
    data : dict[str, object]
        Data at one version.

    Returns
    -------
    dict[str, object]
        The same data.
    """
    return data


class PassCodec:
    """Hands the app's section over as it is stored."""

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

        Raises
        ------
        ValueError
            When the section holds ``bad``.
        """
        if isinstance(data, dict) and 'bad' in data:
            raise ValueError('bad board')
        return data

    def dump(self, *, saved: object) -> dict[str, object]:
        """
        Write the section.

        Parameters
        ----------
        saved : object
            What the app keeps.

        Returns
        -------
        dict[str, object]
            The section unchanged.
        """
        assert isinstance(saved, dict)
        return saved


class FileApp(App[object]):
    """
    An app that keeps a board section and registers some steps.

    Parameters
    ----------
    codec : SectionCodec | None
        Its section codec, or None for none.
    steps : Mapping[int, MigrationStep]
        Its migration steps.
    """

    def __init__(
        self,
        *,
        codec: SectionCodec | None = None,
        steps: Mapping[int, MigrationStep] | None = None,
    ) -> None:
        self._codec = codec
        self._steps = {12: same} if steps is None else steps

    def section(self) -> SectionCodec | None:
        """
        Give the app's codec.

        Returns
        -------
        SectionCodec | None
            The codec.
        """
        return self._codec

    def migrations(self) -> Mapping[int, MigrationStep]:
        """
        Give the app's steps.

        Returns
        -------
        Mapping[int, MigrationStep]
            The steps.
        """
        return self._steps

    def start(
        self, *, section: object | None, stored: bool, device: DeviceState,
    ) -> object:
        """
        Make no state.

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


@pytest.fixture(name='path')
def path_fixture(tmp_path: Path) -> Path:
    """
    Give a state file path in a fresh directory.

    Parameters
    ----------
    tmp_path : Path
        The test's directory.

    Returns
    -------
    Path
        The path, not yet written.
    """
    return tmp_path / 'state.json'


def write(*, path: Path, data: object) -> None:
    """
    Write data as the state file.

    Parameters
    ----------
    path : Path
        The file.
    data : object
        What it holds.
    """
    path.write_text(json.dumps(data), encoding='utf-8')


def test_round_trip_writes_todays_shape(path: Path) -> None:
    """The golden file loads and saves back byte for byte."""
    text = GOLDEN.read_text(encoding='utf-8')
    path.write_text(text, encoding='utf-8')
    states = StateFile(path=path, app=FileApp(codec=PassCodec()))
    loaded = states.load()
    assert loaded.stored
    assert loaded.app == json.loads(text)['board']
    states.save(saved=KitSaved(device=loaded.device, app=loaded.app))
    assert path.read_text(encoding='utf-8') == text


def test_device_settings_round_trip(path: Path) -> None:
    """The device settings written come back as they were."""
    states = StateFile(path=path, app=FileApp())
    states.save(saved=KitSaved(device=KEPT, app=None))
    assert states.load() == Loaded(device=KEPT, app=None, stored=True)


def test_file_without_a_codec_holds_no_board(path: Path) -> None:
    """An app with no section writes only the version and the device."""
    StateFile(path=path, app=FileApp(steps={})).save(
        saved=KitSaved(device=DeviceSettings(), app=None),
    )
    data = json.loads(path.read_text(encoding='utf-8'))
    assert list(data) == ['schema_version', 'device']
    assert data['schema_version'] == 1
    assert data['device']['schema_version'] == 1


def test_missing_file_gives_defaults(path: Path) -> None:
    """No file gives the default settings, not stored."""
    loaded = StateFile(path=path, app=FileApp()).load()
    assert loaded == Loaded(device=DeviceSettings(), app=None, stored=False)


def test_future_schema_falls_back(
    path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """A file newer than the chain gives the defaults and is set aside."""
    write(path=path, data={'schema_version': 14, 'device': {}})
    with caplog.at_level(logging.ERROR):
        loaded = StateFile(path=path, app=FileApp()).load()
    assert loaded == Loaded(device=DeviceSettings(), app=None, stored=False)
    assert 'schema version 14 is from the future' in caplog.text
    assert not path.exists()
    assert len(list(path.parent.glob(f'{BAD_PREFIX}*'))) == 1


def test_unusable_file_is_kept_aside(path: Path) -> None:
    """An unreadable file is renamed, keeping only the newest few."""
    for stamp in range(BAD_KEPT + 1):
        (path.parent / f'{BAD_PREFIX}{stamp}').write_text('old')
    path.write_text('{not json', encoding='utf-8')
    loaded = StateFile(path=path, app=FileApp()).load()
    assert not loaded.stored
    kept = sorted(path.parent.glob(f'{BAD_PREFIX}*'))
    assert len(kept) == BAD_KEPT
    assert '{not json' in [bad.read_text() for bad in kept]


def test_unusable_section_falls_back(path: Path) -> None:
    """A board section the codec refuses gives the defaults."""
    write(path=path, data={
        'schema_version': 13,
        'device': {'schema_version': 1, 'settings': SETTINGS},
        'board': {'bad': True},
    })
    loaded = StateFile(path=path, app=FileApp(codec=PassCodec())).load()
    assert loaded == Loaded(device=DeviceSettings(), app=None, stored=False)


def test_missing_board_section_gives_app_none(path: Path) -> None:
    """A file with no board section hands the app None."""
    write(path=path, data={
        'schema_version': 13,
        'device': {'schema_version': 1, 'settings': SETTINGS},
    })
    loaded = StateFile(path=path, app=FileApp(codec=PassCodec())).load()
    assert loaded == Loaded(device=KEPT, app=None, stored=True)


def test_board_that_is_not_an_object_gives_app_none(path: Path) -> None:
    """A board section that is not an object is reset to none."""
    write(path=path, data={
        'schema_version': 13,
        'device': {'schema_version': 1, 'settings': SETTINGS},
        'board': [1, 2],
    })
    loaded = StateFile(path=path, app=FileApp(codec=PassCodec())).load()
    assert loaded == Loaded(device=KEPT, app=None, stored=True)


def test_unknown_device_key_is_dropped(path: Path) -> None:
    """A stray device setting is dropped and resets nothing."""
    write(path=path, data={
        'schema_version': 13,
        'device': {
            'schema_version': 1,
            'settings': {**SETTINGS, 'auto_rebuild': True},
        },
    })
    loaded = StateFile(path=path, app=FileApp()).load()
    assert loaded == Loaded(device=KEPT, app=None, stored=True)


def test_bad_device_setting_resets_only_itself(path: Path) -> None:
    """One unreadable setting takes its default; the rest are kept."""
    write(path=path, data={
        'schema_version': 13,
        'device': {
            'schema_version': 1,
            'settings': {**SETTINGS, 'clean_after': 7},
        },
    })
    loaded = StateFile(path=path, app=FileApp()).load()
    assert loaded.device == DeviceSettings(
        quiet_mode=QuietMode.APP,
        my_hours=Window(start=time(22, 15), end=time(6)),
        rotate=True,
    )


@pytest.mark.parametrize('section', [None, 'x', {'schema_version': 1}])
def test_unusable_device_section_takes_defaults(
    path: Path, section: object,
) -> None:
    """A device section that is missing or broken takes the defaults."""
    data: dict[str, object] = {'schema_version': 13}
    if section is not None:
        data['device'] = section
    write(path=path, data=data)
    loaded = StateFile(path=path, app=FileApp()).load()
    assert loaded == Loaded(device=DeviceSettings(), app=None, stored=True)


def test_too_new_device_section_falls_back(path: Path) -> None:
    """A device section newer than the kit gives the defaults."""
    write(path=path, data={
        'schema_version': 13,
        'device': {'schema_version': 2, 'settings': SETTINGS},
    })
    loaded = StateFile(path=path, app=FileApp()).load()
    assert not loaded.stored


def test_file_moves_past_a_missing_step(path: Path) -> None:
    """A file at a version with no step loads and saves as the latest."""
    write(path=path, data={
        'schema_version': 1,
        'device': {'schema_version': 1, 'settings': SETTINGS},
    })
    states = StateFile(path=path, app=FileApp(steps={3: same}))
    loaded = states.load()
    assert loaded == Loaded(device=KEPT, app=None, stored=True)
    states.save(saved=KitSaved(device=loaded.device, app=None))
    assert json.loads(path.read_text())['schema_version'] == 4


def test_repeated_step_refuses_at_start(
    path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An app that registers a kit step's number is refused at once."""
    monkeypatch.setattr(
        'paperroute_device.adapters.state_file.KIT_MIGRATIONS', {1: same},
    )
    with pytest.raises(ValueError, match='registered twice'):
        StateFile(path=path, app=FileApp(steps={1: same}))


def test_load_removes_stale_temp_files(path: Path) -> None:
    """Temp files a power cut left behind are deleted on load."""
    stale = path.parent / f'{TEMP_PREFIX}abc'
    stale.write_text('half')
    StateFile(path=path, app=FileApp()).load()
    assert not stale.exists()


def test_directory_at_path_gives_defaults(path: Path) -> None:
    """A directory where the file should be gives the defaults."""
    path.mkdir()
    loaded = StateFile(path=path, app=FileApp()).load()
    assert not loaded.stored


def test_file_that_is_not_an_object_gives_defaults(path: Path) -> None:
    """A JSON list gives the defaults."""
    write(path=path, data=[1])
    assert not StateFile(path=path, app=FileApp()).load().stored
