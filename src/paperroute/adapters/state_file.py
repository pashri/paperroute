"""The pins and view, kept in a schema-versioned JSON file."""

import contextlib
import json
import logging
import os
import tempfile
import time
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from typing import Final

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
)

from paperroute.core.model import TRAINLINK, Pin, RouteKind, StopPlace
from paperroute.core.pin_choices import NICKNAME_MAX, clean_nickname
from paperroute.core.saved import (
    DEFAULT_SAVED,
    LiveView,
    Lookahead,
    QuietMode,
    Quota,
    Rate,
    Saved,
    Settings,
    Window,
)
from paperroute.core.tiles import ZOOMS
from paperroute.ports import Loaded

logger = logging.getLogger(__name__)

DEFAULT_PATH: Final[Path] = Path('/var/lib/paperroute/state.json')
SCHEMA_VERSION: Final[int] = 7
TEMP_PREFIX: Final[str] = '.state.json.'
BAD_PREFIX: Final[str] = 'state.json.bad-'
BAD_KEPT: Final[int] = 2
FILE_MODE: Final[int] = 0o644
CLOCK: Final[str] = r'^([01]\d|2[0-3]):[0-5]\d$'
CLOCK_FORMAT: Final[str] = '%H:%M'
MAX_PEAK_WINDOWS: Final[int] = 2


class StopModel(BaseModel):
    """A stop or station; x and y are metres, or None without a point."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    stop_id: str
    name: str
    x: float | None = Field(allow_inf_nan=False)
    y: float | None = Field(allow_inf_nan=False)


class PinModel(BaseModel):
    """A pin as stored."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    origin: StopModel
    destination: StopModel | None
    kind: RouteKind | None
    routes: tuple[str, ...]
    nickname: str | None = Field(max_length=NICKNAME_MAX)
    only: tuple[str, ...]


class WindowModel(BaseModel):
    """A daily window as stored, each end as ``HH:MM``."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    start: str = Field(pattern=CLOCK)
    end: str = Field(pattern=CLOCK)


class SettingsModel(BaseModel):
    """The Settings page's choices as stored."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    auto_rebuild: bool
    peak_rate: Rate
    offpeak_rate: Rate
    peak_windows: tuple[WindowModel, ...] = Field(
        max_length=MAX_PEAK_WINDOWS,
    )
    quiet_mode: QuietMode
    my_hours: WindowModel
    rotate: bool
    larger_text: bool
    inverted_lists: bool
    alert_lookahead: Lookahead

    @field_validator('peak_windows')
    @classmethod
    def check_peak_windows(
        cls, windows: tuple[WindowModel, ...]
    ) -> tuple[WindowModel, ...]:
        """
        Refuse peak windows that wrap midnight, are empty or overlap.

        Parameters
        ----------
        windows : tuple[WindowModel, ...]
            The stored peak windows.

        Returns
        -------
        tuple[WindowModel, ...]
            The windows, unchanged.

        Raises
        ------
        ValueError
            When a window doesn't end after it starts, or two overlap.
        """
        ordered = sorted(windows, key=lambda window: window.start)
        if any(window.start >= window.end for window in ordered):
            raise ValueError('a peak window must end after it starts')
        if any(
            earlier.end > later.start
            for earlier, later in zip(ordered, ordered[1:])
        ):
            raise ValueError('peak windows must not overlap')
        return windows


class QuotaModel(BaseModel):
    """The API calls counted on one day, as stored."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    day: date | None
    calls: int = Field(ge=0)


class StateModel(BaseModel):
    """The whole file."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    schema_version: int
    pins: tuple[PinModel, ...]
    current: int = Field(ge=0)
    unpinned: PinModel | None
    view: LiveView
    zoom: int = Field(ge=0, le=len(ZOOMS) - 1)
    settings: SettingsModel
    quota: QuotaModel


def stop_to_model(*, place: StopPlace) -> StopModel:
    """
    Convert a place to its stored form.

    Parameters
    ----------
    place : StopPlace
        The place.

    Returns
    -------
    StopModel
        The place with its point split into x and y.
    """
    point = place.point
    return StopModel(
        stop_id=place.stop_id,
        name=place.name,
        x=None if point is None else point[0],
        y=None if point is None else point[1],
    )


def stop_from_model(*, model: StopModel) -> StopPlace:
    """
    Convert a stored place back.

    Parameters
    ----------
    model : StopModel
        The stored place.

    Returns
    -------
    StopPlace
        The place; its point is None unless both x and y are set.
    """
    point = None
    if model.x is not None and model.y is not None:
        point = (model.x, model.y)
    return StopPlace(stop_id=model.stop_id, name=model.name, point=point)


def pin_to_model(*, pin: Pin) -> PinModel:
    """
    Convert a pin to its stored form.

    Parameters
    ----------
    pin : Pin
        The pin; whether a place is missing is not stored.

    Returns
    -------
    PinModel
        The stored pin.
    """
    destination = pin.destination
    return PinModel(
        origin=stop_to_model(place=pin.origin),
        destination=(
            None
            if destination is None
            else stop_to_model(place=destination)
        ),
        kind=pin.kind,
        routes=pin.routes,
        nickname=pin.nickname,
        only=pin.only,
    )


def pin_from_model(*, model: PinModel) -> Pin:
    """
    Convert a stored pin back.

    Parameters
    ----------
    model : PinModel
        The stored pin.

    Returns
    -------
    Pin
        The pin, not marked missing.
    """
    destination = model.destination
    return Pin(
        origin=stop_from_model(model=model.origin),
        destination=(
            None
            if destination is None
            else stop_from_model(model=destination)
        ),
        kind=model.kind,
        routes=model.routes,
        nickname=model.nickname,
        only=model.only,
    )


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


def settings_to_model(*, settings: Settings) -> SettingsModel:
    """
    Convert the settings to their stored form.

    Parameters
    ----------
    settings : Settings
        The settings.

    Returns
    -------
    SettingsModel
        The stored settings.
    """
    return SettingsModel(
        auto_rebuild=settings.auto_rebuild,
        peak_rate=settings.peak_rate,
        offpeak_rate=settings.offpeak_rate,
        peak_windows=tuple(
            window_to_model(window=window)
            for window in settings.peak_windows
        ),
        quiet_mode=settings.quiet_mode,
        my_hours=window_to_model(window=settings.my_hours),
        rotate=settings.rotate,
        larger_text=settings.larger_text,
        inverted_lists=settings.inverted_lists,
        alert_lookahead=settings.alert_lookahead,
    )


def settings_from_model(*, model: SettingsModel) -> Settings:
    """
    Convert stored settings back.

    Parameters
    ----------
    model : SettingsModel
        The stored settings.

    Returns
    -------
    Settings
        The settings.
    """
    return Settings(
        auto_rebuild=model.auto_rebuild,
        peak_rate=model.peak_rate,
        offpeak_rate=model.offpeak_rate,
        peak_windows=tuple(
            window_from_model(model=window) for window in model.peak_windows
        ),
        quiet_mode=model.quiet_mode,
        my_hours=window_from_model(model=model.my_hours),
        rotate=model.rotate,
        larger_text=model.larger_text,
        inverted_lists=model.inverted_lists,
        alert_lookahead=model.alert_lookahead,
    )


def default_settings() -> dict[str, object]:
    """
    Give the default settings as stored.

    Returns
    -------
    dict[str, object]
        The default settings as JSON data.
    """
    return settings_to_model(settings=Settings()).model_dump(mode='json')


def to_model(*, saved: Saved) -> StateModel:
    """
    Convert the saved state to its stored form.

    Parameters
    ----------
    saved : Saved
        The pins and view.

    Returns
    -------
    StateModel
        The model to write.
    """
    unpinned = saved.unpinned
    return StateModel(
        schema_version=SCHEMA_VERSION,
        pins=tuple(pin_to_model(pin=pin) for pin in saved.pins),
        current=saved.current,
        unpinned=None if unpinned is None else pin_to_model(pin=unpinned),
        view=saved.view,
        zoom=saved.zoom,
        settings=settings_to_model(settings=saved.settings),
        quota=QuotaModel(day=saved.quota.day, calls=saved.quota.calls),
    )


def from_model(*, model: StateModel) -> Saved:
    """
    Convert a stored state back, repairing what can be repaired.

    Parameters
    ----------
    model : StateModel
        The stored state.

    Returns
    -------
    Saved
        The state. The current index is clamped into range, and no pins
        with no unpinned place gives the default place.
    """
    pins = tuple(pin_from_model(model=pin) for pin in model.pins)
    unpinned = (
        None
        if model.unpinned is None
        else pin_from_model(model=model.unpinned)
    )
    if not pins and unpinned is None:
        unpinned = DEFAULT_SAVED.unpinned
    return Saved(
        pins=pins,
        current=min(model.current, max(len(pins) - 1, 0)),
        unpinned=unpinned,
        view=model.view,
        zoom=model.zoom,
        settings=settings_from_model(model=model.settings),
        quota=Quota(day=model.quota.day, calls=model.quota.calls),
    )


def coach_pin(*, pin: object) -> object:
    """
    Move one stored Regional pin to Coach, or to Train for a TrainLink line.

    Parameters
    ----------
    pin : object
        A stored pin; anything that is not a Regional pin is returned as
        it is.

    Returns
    -------
    object
        The pin with its kind rewritten.
    """
    if not isinstance(pin, dict) or pin.get('kind') != 'regional':
        return pin
    routes = pin.get('routes')
    trainlink = (
        isinstance(routes, list)
        and bool(routes)
        and all(route in TRAINLINK for route in routes)
    )
    return {**pin, 'kind': 'train' if trainlink else 'coach'}


def regional_to_coach(data: dict[str, object]) -> dict[str, object]:
    """
    Rewrite every Regional pin, and the unpinned place, as Coach or Train.

    Parameters
    ----------
    data : dict[str, object]
        Version 1 data.

    Returns
    -------
    dict[str, object]
        The data with its pins rewritten.
    """
    pins = data.get('pins')
    return {
        **data,
        'pins': (
            [coach_pin(pin=pin) for pin in pins]
            if isinstance(pins, list)
            else pins
        ),
        'unpinned': coach_pin(pin=data.get('unpinned')),
    }


def add_settings(data: dict[str, object]) -> dict[str, object]:
    """
    Add the settings, with the timetable rebuilt automatically.

    Parameters
    ----------
    data : dict[str, object]
        Version 2 data.

    Returns
    -------
    dict[str, object]
        The data with default settings.
    """
    return {**data, 'settings': {'auto_rebuild': True}}


def add_quota(data: dict[str, object]) -> dict[str, object]:
    """
    Add the quota, with no calls counted.

    Parameters
    ----------
    data : dict[str, object]
        Version 3 data.

    Returns
    -------
    dict[str, object]
        The data with an empty quota.
    """
    return {**data, 'quota': {'day': None, 'calls': 0}}


def add_display_settings(data: dict[str, object]) -> dict[str, object]:
    """
    Add the rate, quiet-hours and display settings at their defaults.

    Parameters
    ----------
    data : dict[str, object]
        Version 4 data.

    Returns
    -------
    dict[str, object]
        The data with every new setting at its default; settings that
        are not an object are left for validation to fail.
    """
    settings = data.get('settings')
    if not isinstance(settings, dict):
        return data
    return {**data, 'settings': {**default_settings(), **settings}}


def add_pin_fields(data: dict[str, object]) -> dict[str, object]:
    """
    Give every pin an empty nickname and route filter.

    Parameters
    ----------
    data : dict[str, object]
        Version 5 data.

    Returns
    -------
    dict[str, object]
        The data with `nickname` None and `only` empty on each pin and
        on the unpinned place; anything that is not an object is left
        for validation to fail.
    """
    fields: dict[str, object] = {'nickname': None, 'only': []}
    pins = data.get('pins')
    unpinned = data.get('unpinned')
    return {
        **data,
        'pins': (
            [
                {**fields, **pin} if isinstance(pin, dict) else pin
                for pin in pins
            ]
            if isinstance(pins, list)
            else pins
        ),
        'unpinned': (
            {**fields, **unpinned} if isinstance(unpinned, dict) else unpinned
        ),
    }


def add_alert_settings(data: dict[str, object]) -> dict[str, object]:
    """
    Add the alert lookahead at its default.

    Parameters
    ----------
    data : dict[str, object]
        Version 6 data.

    Returns
    -------
    dict[str, object]
        The data with every new setting at its default; settings that
        are not an object are left for validation to fail.
    """
    return add_display_settings(data)


MIGRATIONS: Final[
    dict[int, Callable[[dict[str, object]], dict[str, object]]]
] = {
    1: regional_to_coach,
    2: add_settings,
    3: add_quota,
    4: add_display_settings,
    5: add_pin_fields,
    6: add_alert_settings,
}


def migrate(*, data: dict[str, object]) -> dict[str, object]:
    """
    Bring stored data up to the current schema version.

    Parameters
    ----------
    data : dict[str, object]
        The decoded file.

    Returns
    -------
    dict[str, object]
        The data at `SCHEMA_VERSION`.

    Raises
    ------
    ValueError
        When the version is missing, above `SCHEMA_VERSION`, or has no
        migration step.
    """
    version = data.get('schema_version')
    if not isinstance(version, int) or isinstance(version, bool):
        raise ValueError(f'bad schema version {version!r}')
    if version > SCHEMA_VERSION:
        raise ValueError(f'schema version {version} is from the future')
    while version < SCHEMA_VERSION:
        if version not in MIGRATIONS:
            raise ValueError(f'no migration from version {version}')
        data = MIGRATIONS[version](data)
        version += 1
        data['schema_version'] = version
    return data


def repaired_quota(*, data: dict[str, object]) -> dict[str, object]:
    """
    Reset a quota that cannot be read, so the pins are kept.

    Parameters
    ----------
    data : dict[str, object]
        Migrated data.

    Returns
    -------
    dict[str, object]
        The data; with no calls counted when its quota is not valid.
    """
    try:
        QuotaModel.model_validate(data.get('quota'))
    except ValidationError:
        logger.warning('state.json quota unusable, resetting it')
        return {**data, 'quota': {'day': None, 'calls': 0}}
    return data


def repaired_settings(*, data: dict[str, object]) -> dict[str, object]:
    """
    Reset each setting that cannot be read, so the rest are kept.

    Parameters
    ----------
    data : dict[str, object]
        Migrated data.

    Returns
    -------
    dict[str, object]
        The data with each bad setting at its default and unknown ones
        dropped; all defaults when the settings are not an object. The
        peak windows are reset as a whole when any of them is bad.
    """
    settings = data.get('settings')
    if not isinstance(settings, dict):
        logger.warning('state.json settings unusable, resetting them')
        return {**data, 'settings': default_settings()}
    try:
        SettingsModel.model_validate(settings)
    except ValidationError as error:
        bad = {item['loc'][0] for item in error.errors() if item['loc']}
        logger.warning('state.json settings %s unusable, resetting', bad)
        defaults = default_settings()
        kept = {
            name: value
            for name, value in settings.items()
            if name in defaults and name not in bad
        }
        return {**data, 'settings': {**defaults, **kept}}
    return data


def repaired_pin(*, pin: object) -> dict[str, object] | None:
    """
    Repair one stored pin's nickname and route filter.

    Parameters
    ----------
    pin : object
        The stored pin.

    Returns
    -------
    dict[str, object] | None
        The pin with a bad nickname reset to None (a long one is
        cleaned) and a bad filter reset to empty; None when the pin is
        still not valid.
    """
    if not isinstance(pin, dict):
        return None
    nickname = pin.get('nickname')
    only = pin.get('only')
    fixed = {
        **pin,
        'nickname': (
            clean_nickname(text=nickname)
            if isinstance(nickname, str)
            else None
        ),
        'only': (
            only
            if isinstance(only, list)
            and all(isinstance(route, str) for route in only)
            else []
        ),
    }
    try:
        PinModel.model_validate(fixed)
    except ValidationError:
        logger.warning('state.json pin unusable, dropping it')
        return None
    return fixed


def repaired_pins(*, data: dict[str, object]) -> dict[str, object]:
    """
    Repair each pin, dropping only the ones that cannot be read.

    Parameters
    ----------
    data : dict[str, object]
        Migrated data.

    Returns
    -------
    dict[str, object]
        The data with each pin repaired and unusable pins dropped, and
        the unpinned place None when it is unusable. Data whose pins are
        not a list is left for validation to fail.
    """
    pins = data.get('pins')
    if not isinstance(pins, list):
        return data
    kept = [repaired_pin(pin=pin) for pin in pins]
    unpinned = data.get('unpinned')
    return {
        **data,
        'pins': [pin for pin in kept if pin is not None],
        'unpinned': (
            None if unpinned is None else repaired_pin(pin=unpinned)
        ),
    }


def write_atomic(
    *,
    path: Path,
    text: str,
    sync: Callable[[Path], None] | None = None,
    mode: int = FILE_MODE,
    prefix: str = TEMP_PREFIX,
) -> None:
    """
    Replace a file so a reader sees the old text or the new, never a mix.

    Parameters
    ----------
    path : Path
        The file.
    text : str
        The new contents.
    sync : Callable[[Path], None] | None
        Flushes the directory after the rename; `sync_directory` when None.
        A failure here is logged, not raised, since the new file is
        already in place.
    mode : int
        Permission bits of the new file; readable by everyone by default.
    prefix : str
        Name prefix of the temp file written beside it.

    Raises
    ------
    OSError
        When writing or renaming fails; the temp file is removed.
    """
    descriptor, temp_name = tempfile.mkstemp(
        prefix=prefix,
        dir=path.parent,
    )
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
            os.fchmod(handle.fileno(), mode)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temp_name)
        raise
    try:
        (sync or sync_directory)(path.parent)
    except OSError:
        logger.warning('%s directory not synced', path.name, exc_info=True)


def sync_directory(directory: Path) -> None:
    """
    Flush a directory's entries to disk.

    Parameters
    ----------
    directory : Path
        The directory.
    """
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


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


class JsonStateRepository:
    """
    The saved state in a JSON file.

    Parameters
    ----------
    path : Path | None
        The file; `DEFAULT_PATH` when None.
    """

    def __init__(self, *, path: Path | None = None) -> None:
        self.path = path or DEFAULT_PATH

    def load(self) -> Loaded:
        """
        Read the file, falling back to the defaults when it is unusable.

        Returns
        -------
        Loaded
            The saved state; `stored` is False without a usable file.
        """
        self.remove_stale_temps()
        if not self.path.exists() and not self.path.is_symlink():
            return Loaded(saved=DEFAULT_SAVED, stored=False)
        try:
            if not self.path.is_file():
                raise OSError('state.json is not a regular file')
            text = self.path.read_text(encoding='utf-8')
        except (OSError, UnicodeDecodeError):
            return self.defaults_after_error()
        try:
            data = json.loads(text)
            if not isinstance(data, dict):
                raise ValueError('state.json is not an object')
            migrated = repaired_pins(
                data=repaired_settings(
                    data=repaired_quota(data=migrate(data=data)),
                ),
            )
            model = StateModel.model_validate(migrated)
        except (ValueError, RecursionError):
            return self.defaults_after_error()
        return Loaded(saved=from_model(model=model), stored=True)

    def save(self, *, saved: Saved) -> None:
        """
        Write the saved state.

        Parameters
        ----------
        saved : Saved
            The pins and view.

        Raises
        ------
        OSError
            When the file can't be written.
        """
        text = to_model(saved=saved).model_dump_json(indent=2)
        write_atomic(path=self.path, text=text)

    def remove_stale_temps(self) -> None:
        """Delete temp files a power cut left behind."""
        for stale in self.path.parent.glob(f'{TEMP_PREFIX}*'):
            with contextlib.suppress(OSError):
                stale.unlink()

    def defaults_after_error(self) -> Loaded:
        """
        Log the failure, move the file aside and give the defaults.

        The file is renamed rather than left in place, so the first save
        of the defaults doesn't destroy the pins it holds.

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
        return Loaded(saved=DEFAULT_SAVED, stored=False)

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
