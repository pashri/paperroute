"""The device settings and the app's section, kept in one JSON file."""

import contextlib
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Final

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
)

from paperroute_device.adapters.atomic_file import write_atomic
from paperroute_device.adapters.migrations import (
    KIT_MIGRATIONS,
    chain,
    latest_version,
    migrate,
)
from paperroute_device.core.app import App
from paperroute_device.core.device_settings import (
    CLEAN_CHOICES,
    DeviceSettings,
    QuietMode,
    Window,
)
from paperroute_device.core.device_state import KitSaved
from paperroute_device.core.sections import (
    DEVICE_MIGRATIONS,
    DEVICE_SCHEMA,
    migrate_section,
)
from paperroute_hardware.ports import CLEAN_AFTER

logger = logging.getLogger(__name__)

DEFAULT_PATH: Final[Path] = Path('/var/lib/paperroute/state.json')
TEMP_PREFIX: Final[str] = '.state.json.'
BAD_PREFIX: Final[str] = 'state.json.bad-'
BAD_KEPT: Final[int] = 2
CLOCK: Final[str] = r'^([01]\d|2[0-3]):[0-5]\d$'
CLOCK_FORMAT: Final[str] = '%H:%M'


class WindowModel(BaseModel):
    """A daily window as stored, each end as ``HH:MM``."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    start: str = Field(pattern=CLOCK)
    end: str = Field(pattern=CLOCK)


class DeviceSettingsModel(BaseModel):
    """
    The device's own Settings page choices as stored.

    The cleaning count and the knob swap have defaults, so files stored
    before them load.
    """

    model_config = ConfigDict(extra='forbid', frozen=True)

    quiet_mode: QuietMode
    my_hours: WindowModel
    rotate: bool
    larger_text: bool
    inverted_lists: bool
    clean_after: int = CLEAN_AFTER
    swap_knobs: bool = False

    @field_validator('clean_after')
    @classmethod
    def check_clean_after(cls, count: int) -> int:
        """
        Refuse a cleaning count the Settings page never offers.

        Parameters
        ----------
        count : int
            The stored count.

        Returns
        -------
        int
            The count, unchanged.

        Raises
        ------
        ValueError
            When the count is not one of `CLEAN_CHOICES`.
        """
        if count not in CLEAN_CHOICES:
            raise ValueError(f'clean_after must be one of {CLEAN_CHOICES}')
        return count


DEVICE_SETTING_NAMES: Final[frozenset[str]] = frozenset(
    DeviceSettingsModel.model_fields,
)


class DeviceSectionModel(BaseModel):
    """What the device keeps for itself, as stored."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    schema_version: int
    settings: DeviceSettingsModel


class StateModel(BaseModel):
    """The whole file; the app's section is kept as its codec wrote it."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    schema_version: int
    device: DeviceSectionModel
    board: dict[str, object] | None = None


@dataclass(frozen=True)
class Loaded:
    """
    What the state file held.

    Attributes
    ----------
    device : DeviceSettings
        The device settings, or the defaults.
    app : object | None
        The app's section as its codec read it; None without a section.
    stored : bool
        False when there was no file or it fell back to the defaults.
    """

    device: DeviceSettings
    app: object | None
    stored: bool


def window_to_model(*, window: Window) -> WindowModel:
    """
    Convert a window to its stored form.

    Parameters
    ----------
    window : Window
        The window.

    Returns
    -------
    WindowModel
        The window with each end as ``HH:MM``.
    """
    return WindowModel(
        start=window.start.strftime(CLOCK_FORMAT),
        end=window.end.strftime(CLOCK_FORMAT),
    )


def window_from_model(*, model: WindowModel) -> Window:
    """
    Convert a stored window back.

    Parameters
    ----------
    model : WindowModel
        The stored window.

    Returns
    -------
    Window
        The window.
    """
    return Window(
        start=datetime.strptime(model.start, CLOCK_FORMAT).time(),
        end=datetime.strptime(model.end, CLOCK_FORMAT).time(),
    )


def device_settings_to_model(
    *, settings: DeviceSettings,
) -> DeviceSettingsModel:
    """
    Convert the device's settings to their stored form.

    Parameters
    ----------
    settings : DeviceSettings
        The device's settings.

    Returns
    -------
    DeviceSettingsModel
        The stored settings.
    """
    return DeviceSettingsModel(
        quiet_mode=settings.quiet_mode,
        my_hours=window_to_model(window=settings.my_hours),
        rotate=settings.rotate,
        larger_text=settings.larger_text,
        inverted_lists=settings.inverted_lists,
        clean_after=settings.clean_after,
        swap_knobs=settings.swap_knobs,
    )


def device_settings_from_model(
    *, model: DeviceSettingsModel,
) -> DeviceSettings:
    """
    Convert the device's stored settings back.

    Parameters
    ----------
    model : DeviceSettingsModel
        The stored settings.

    Returns
    -------
    DeviceSettings
        The device's settings.
    """
    return DeviceSettings(
        quiet_mode=model.quiet_mode,
        my_hours=window_from_model(model=model.my_hours),
        rotate=model.rotate,
        larger_text=model.larger_text,
        inverted_lists=model.inverted_lists,
        clean_after=model.clean_after,
        swap_knobs=model.swap_knobs,
    )


def device_section(*, settings: DeviceSettings) -> DeviceSectionModel:
    """
    Convert the device's settings to its stored section.

    Parameters
    ----------
    settings : DeviceSettings
        The device's settings.

    Returns
    -------
    DeviceSectionModel
        The section at `DEVICE_SCHEMA`.
    """
    return DeviceSectionModel(
        schema_version=DEVICE_SCHEMA,
        settings=device_settings_to_model(settings=settings),
    )


def repaired_settings(
    *,
    data: dict[str, object],
    model: type[BaseModel],
    defaults: dict[str, object],
) -> dict[str, object]:
    """
    Reset each setting that cannot be read, so the rest are kept.

    Parameters
    ----------
    data : dict[str, object]
        A migrated section.
    model : type[BaseModel]
        The model its settings must match.
    defaults : dict[str, object]
        Its default settings as stored.

    Returns
    -------
    dict[str, object]
        The section with each bad setting at its default and unknown ones
        dropped; all defaults when the settings are not an object.
    """
    settings = data.get('settings')
    if not isinstance(settings, dict):
        logger.warning('state.json settings unusable, resetting them')
        return {**data, 'settings': defaults}
    try:
        model.model_validate(settings)
    except ValidationError as error:
        bad = {item['loc'][0] for item in error.errors() if item['loc']}
        logger.warning('state.json settings %s unusable, resetting', bad)
        kept = {
            name: value
            for name, value in settings.items()
            if name in defaults and name not in bad
        }
        return {**data, 'settings': {**defaults, **kept}}
    return data


def repaired_device(*, section: object) -> dict[str, object]:
    """
    Repair the migrated device section.

    Parameters
    ----------
    section : object
        The migrated section, as stored.

    Returns
    -------
    dict[str, object]
        The section with unknown settings dropped quietly, before
        validation, and each bad one reset; all defaults when it is
        missing or not an object.
    """
    defaults = device_section(settings=DeviceSettings()).model_dump(
        mode='json',
    )
    if not isinstance(section, dict):
        logger.warning('state.json device section unusable, resetting it')
        return defaults
    settings = section.get('settings')
    if isinstance(settings, dict):
        section = {**section, 'settings': {
            name: value
            for name, value in settings.items()
            if name in DEVICE_SETTING_NAMES
        }}
    return repaired_settings(
        data=section, model=DeviceSettingsModel, defaults=defaults['settings'],
    )


def migrated_device(*, section: object) -> dict[str, object]:
    """
    Bring the device section up to its latest version and repair it.

    Parameters
    ----------
    section : object
        The section, as stored.

    Returns
    -------
    dict[str, object]
        The section at `DEVICE_SCHEMA`, repaired.

    Raises
    ------
    ValueError
        When its version is missing, too new or has no step.
    """
    if isinstance(section, dict):
        section = migrate_section(
            data=section,
            steps=DEVICE_MIGRATIONS,
            latest=DEVICE_SCHEMA,
            name='device',
        )
    return repaired_device(section=section)


def board_of(*, section: object) -> dict[str, object] | None:
    """
    Take the app's section as stored.

    Parameters
    ----------
    section : object
        The section, as stored.

    Returns
    -------
    dict[str, object] | None
        The section; None when it is missing or not an object.
    """
    if section is None or isinstance(section, dict):
        return section
    logger.warning('state.json board section unusable, resetting it')
    return None


def bad_stamp(path: Path) -> int:
    """
    Give the time a moved-aside file was moved.

    Parameters
    ----------
    path : Path
        A ``state.json.bad-<seconds>`` file.

    Returns
    -------
    int
        The seconds in its name; 0 when they aren't a number.
    """
    stamp = path.name.removeprefix(BAD_PREFIX)
    return int(stamp) if stamp.isdecimal() else 0


def state_text(*, path: Path) -> str:
    """
    Read the state file's text.

    Parameters
    ----------
    path : Path
        The file.

    Returns
    -------
    str
        Its contents.

    Raises
    ------
    OSError
        When it is not a regular file or can't be read.
    UnicodeDecodeError
        When it is not UTF-8.
    """
    if not path.is_file():
        raise OSError('state.json is not a regular file')
    return path.read_text(encoding='utf-8')


class StateFile:
    """
    The device settings and the app's section in a JSON file.

    The file has one schema version, migrated by one chain: the kit's
    steps and the app's. A version with no step is passed unchanged.

    Parameters
    ----------
    path : Path
        The file.
    app : App[Any]
        The running app: its steps and its section's codec.

    Raises
    ------
    ValueError
        When the kit and the app register the same step number.
    """

    def __init__(self, *, path: Path, app: App[Any]) -> None:
        self.path = path
        self.codec = app.section()
        self.chain = chain(kit=KIT_MIGRATIONS, app=app.migrations())
        self.latest = latest_version(chain=self.chain)

    def load(self) -> Loaded:
        """
        Read the file, falling back to the defaults when it is unusable.

        Returns
        -------
        Loaded
            What it held; `stored` is False without a usable file.
        """
        self.remove_stale_temps()
        if not self.path.exists() and not self.path.is_symlink():
            return Loaded(device=DeviceSettings(), app=None, stored=False)
        try:
            model = self.model_of(text=state_text(path=self.path))
            app = self.app_of(section=model.board)
        except (OSError, UnicodeDecodeError, ValueError, RecursionError):
            return self.defaults_after_error()
        device = device_settings_from_model(model=model.device.settings)
        return Loaded(device=device, app=app, stored=True)

    def model_of(self, *, text: str) -> StateModel:
        """
        Parse the file's text, migrating and repairing it first.

        Parameters
        ----------
        text : str
            The file's contents.

        Returns
        -------
        StateModel
            The stored state.

        Raises
        ------
        ValueError
            When it is not JSON, not an object, or not valid after repair.
        RecursionError
            When it is nested too deeply to parse.
        """
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError('state.json is not an object')
        migrated = migrate(data=data, chain=self.chain, latest=self.latest)
        return StateModel.model_validate({
            **migrated,
            'device': migrated_device(section=migrated.get('device')),
            'board': board_of(section=migrated.get('board')),
        })

    def app_of(self, *, section: dict[str, object] | None) -> object | None:
        """
        Read the app's section through its codec.

        Parameters
        ----------
        section : dict[str, object] | None
            The section as stored, or None.

        Returns
        -------
        object | None
            What the codec read; None without a section or a codec.

        Raises
        ------
        ValueError
            When the codec finds the section unusable.
        """
        if section is None or self.codec is None:
            return None
        return self.codec.load(data=section)

    def save(self, *, saved: KitSaved) -> None:
        """
        Write the device settings and the app's section.

        Parameters
        ----------
        saved : KitSaved
            What the kit and the app keep.

        Raises
        ------
        OSError
            When the file can't be written.
        """
        board = (
            None
            if saved.app is None or self.codec is None
            else self.codec.dump(saved=saved.app)
        )
        model = StateModel(
            schema_version=self.latest,
            device=device_section(settings=saved.device),
            board=board,
        )
        text = model.model_dump_json(
            indent=2, exclude=None if board is not None else {'board'},
        )
        write_atomic(path=self.path, text=text, prefix=TEMP_PREFIX)

    def remove_stale_temps(self) -> None:
        """Delete temp files a power cut left behind."""
        for stale in self.path.parent.glob(f'{TEMP_PREFIX}*'):
            with contextlib.suppress(OSError):
                stale.unlink()

    def defaults_after_error(self) -> Loaded:
        """
        Log the failure, move the file aside and give the defaults.

        The file is renamed rather than left in place, so the first save
        of the defaults doesn't destroy what it holds.

        Returns
        -------
        Loaded
            The defaults, not stored.
        """
        logger.error('state.json unusable, using defaults', exc_info=True)
        with contextlib.suppress(OSError):
            self.path.replace(
                self.path.with_name(f'{BAD_PREFIX}{int(time.time())}'),
            )
            self.remove_old_bad_files()
        return Loaded(device=DeviceSettings(), app=None, stored=False)

    def remove_old_bad_files(self) -> None:
        """Delete all but the newest `BAD_KEPT` moved-aside files."""
        bad = sorted(
            self.path.parent.glob(f'{BAD_PREFIX}*'),
            key=bad_stamp,
            reverse=True,
        )
        for old in bad[BAD_KEPT:]:
            with contextlib.suppress(OSError):
                old.unlink()
