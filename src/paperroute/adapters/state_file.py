"""The pins and view, kept in a schema-versioned JSON file."""

import contextlib
import json
import logging
import os
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from paperroute.core.model import TRAINLINK, Pin, RouteKind, StopPlace
from paperroute.core.saved import DEFAULT_SAVED, LiveView, Saved
from paperroute.core.tiles import ZOOMS
from paperroute.ports import Loaded

logger = logging.getLogger(__name__)

DEFAULT_PATH: Final[Path] = Path('/var/lib/paperroute/state.json')
SCHEMA_VERSION: Final[int] = 2
TEMP_PREFIX: Final[str] = '.state.json.'
BAD_PREFIX: Final[str] = 'state.json.bad-'
BAD_KEPT: Final[int] = 2
FILE_MODE: Final[int] = 0o644


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


class StateModel(BaseModel):
    """The whole file."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    schema_version: int
    pins: tuple[PinModel, ...]
    current: int = Field(ge=0)
    unpinned: PinModel | None
    view: LiveView
    zoom: int = Field(ge=0, le=len(ZOOMS) - 1)


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
    )


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


MIGRATIONS: Final[
    dict[int, Callable[[dict[str, object]], dict[str, object]]]
] = {1: regional_to_coach}


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


def write_atomic(
    *,
    path: Path,
    text: str,
    sync: Callable[[Path], None] | None = None,
) -> None:
    """
    Replace a file so a reader sees the old text or the new, never a mix.

    The file is readable by everyone.

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

    Raises
    ------
    OSError
        When writing or renaming fails; the temp file is removed.
    """
    descriptor, temp_name = tempfile.mkstemp(
        prefix=TEMP_PREFIX,
        dir=path.parent,
    )
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
            os.fchmod(handle.fileno(), FILE_MODE)
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
        logger.warning('state.json directory not synced', exc_info=True)


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
            model = StateModel.model_validate(migrate(data=data))
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
