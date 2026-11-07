"""The pins and view, kept in a schema-versioned JSON file."""

import contextlib
import json
import logging
import time
from datetime import date
from pathlib import Path
from typing import Final

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
)

from paperroute.adapters.state_auto import (
    EMPTY_AUTO,
    AutoWindowModel,
    OccurrenceModel,
    occurrence_from_model,
    occurrence_to_model,
    repaired_windows,
)
from paperroute.adapters.state_auto import (
    window_from_model as auto_window_from_model,
)
from paperroute.adapters.state_auto import (
    window_to_model as auto_window_to_model,
)
from paperroute.adapters.state_change import (
    ALLOW_MAX,
    ALLOW_MIN,
    repaired_never,
    repaired_ways,
    route_names,
)
from paperroute.adapters.state_migrations import SCHEMA_VERSION, migrate
from paperroute.adapters.state_settings import (
    SettingsModel,
    default_settings,
    settings_from_model,
    settings_to_model,
)
from paperroute.core.auto_model import AutoState, Before
from paperroute.core.model import (
    MAX_CHANGES,
    SYDNEY,
    Change,
    Pin,
    RouteKind,
    StopPlace,
    Way,
)
from paperroute.core.pin_choices import NICKNAME_MAX, clean_nickname
from paperroute.core.saved import DEFAULT_SAVED, LiveView, Quota, Saved
from paperroute.ports import Loaded
from paperroute_device.adapters.atomic_file import write_atomic

logger = logging.getLogger(__name__)

DEFAULT_PATH: Final[Path] = Path('/var/lib/paperroute/state.json')
TEMP_PREFIX: Final[str] = '.state.json.'
BAD_PREFIX: Final[str] = 'state.json.bad-'
BAD_KEPT: Final[int] = 2


class StopModel(BaseModel):
    """A stop or station; x and y are metres, or None without a point."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    stop_id: str
    name: str
    x: float | None = Field(allow_inf_nan=False)
    y: float | None = Field(allow_inf_nan=False)


class ChangeModel(BaseModel):
    """A connection pin's change, as stored."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    alight: StopModel
    board: StopModel
    allow: int = Field(ge=ALLOW_MIN, le=ALLOW_MAX)


class WayModel(BaseModel):
    """One way to go of a connection pin, as stored."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    changes: tuple[ChangeModel, ...] = Field(
        min_length=1, max_length=MAX_CHANGES,
    )
    ticked: bool


class PinModel(BaseModel):
    """A pin as stored."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    origin: StopModel
    destination: StopModel | None
    kind: RouteKind | None
    routes: tuple[str, ...]
    nickname: str | None = Field(max_length=NICKNAME_MAX)
    only: tuple[str, ...]
    windows: tuple[AutoWindowModel, ...]
    ways: tuple[WayModel, ...]
    never: tuple[str, ...]


class BeforeModel(BaseModel):
    """The place shown before a window started, as stored."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    place: PinModel
    pinned: bool


class AutoModel(BaseModel):
    """What the auto pin rules have done, as stored."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    occurrence: OccurrenceModel | None
    before: BeforeModel | None
    overridden_at: AwareDatetime | None


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
    settings: SettingsModel
    quota: QuotaModel
    auto: AutoModel


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


def change_to_model(*, change: Change) -> ChangeModel:
    """
    Convert a change to its stored form.

    Parameters
    ----------
    change : Change
        The change.

    Returns
    -------
    ChangeModel
        The stored change.
    """
    return ChangeModel(
        alight=stop_to_model(place=change.alight),
        board=stop_to_model(place=change.board),
        allow=change.allow,
    )


def change_from_model(*, model: ChangeModel) -> Change:
    """
    Convert a stored change back.

    Parameters
    ----------
    model : ChangeModel
        The stored change.

    Returns
    -------
    Change
        The change.
    """
    return Change(
        alight=stop_from_model(model=model.alight),
        board=stop_from_model(model=model.board),
        allow=model.allow,
    )


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
        windows=tuple(
            auto_window_to_model(window=window) for window in pin.windows
        ),
        ways=tuple(
            WayModel(
                changes=tuple(
                    change_to_model(change=change) for change in way.changes
                ),
                ticked=way.ticked,
            )
            for way in pin.ways
        ),
        never=pin.never,
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
        windows=tuple(
            auto_window_from_model(model=window) for window in model.windows
        ),
        ways=tuple(
            Way(
                changes=tuple(
                    change_from_model(model=change) for change in way.changes
                ),
                ticked=way.ticked,
            )
            for way in model.ways
        ),
        never=model.never,
    )


def auto_to_model(*, auto: AutoState) -> AutoModel:
    """
    Convert the auto pin state to its stored form.

    Parameters
    ----------
    auto : AutoState
        The auto pin state.

    Returns
    -------
    AutoModel
        The stored state.
    """
    occurrence = auto.occurrence
    before = auto.before
    return AutoModel(
        occurrence=(
            None
            if occurrence is None
            else occurrence_to_model(occurrence=occurrence)
        ),
        before=(
            None
            if before is None
            else BeforeModel(
                place=pin_to_model(pin=before.place), pinned=before.pinned,
            )
        ),
        overridden_at=auto.overridden_at,
    )


def auto_from_model(*, model: AutoModel) -> AutoState:
    """
    Convert a stored auto pin state back.

    Parameters
    ----------
    model : AutoModel
        The stored state.

    Returns
    -------
    AutoState
        The state, its times in Sydney.
    """
    occurrence = model.occurrence
    before = model.before
    overridden_at = model.overridden_at
    return AutoState(
        occurrence=(
            None
            if occurrence is None
            else occurrence_from_model(model=occurrence)
        ),
        before=(
            None
            if before is None
            else Before(
                place=pin_from_model(model=before.place),
                pinned=before.pinned,
            )
        ),
        overridden_at=(
            None if overridden_at is None else overridden_at.astimezone(SYDNEY)
        ),
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
        settings=settings_to_model(settings=saved.settings),
        quota=QuotaModel(day=saved.quota.day, calls=saved.quota.calls),
        auto=auto_to_model(auto=saved.auto),
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
        settings=settings_from_model(model=model.settings),
        quota=Quota(day=model.quota.day, calls=model.quota.calls),
        auto=auto_from_model(model=model.auto),
    )


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


def repaired_auto(*, data: dict[str, object]) -> dict[str, object]:
    """
    Reset an auto block that cannot be read, so the rest is kept.

    Parameters
    ----------
    data : dict[str, object]
        Migrated data.

    Returns
    -------
    dict[str, object]
        The data; with every auto field None when its auto block is not
        valid.
    """
    try:
        AutoModel.model_validate(data.get('auto'))
    except ValidationError:
        logger.warning('state.json auto pin state unusable, resetting it')
        return {**data, 'auto': dict(EMPTY_AUTO)}
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


def readable_way(way: object) -> bool:
    """
    Say whether one stored way is valid.

    Parameters
    ----------
    way : object
        The stored way.

    Returns
    -------
    bool
        True when it validates; an unreadable way is logged.
    """
    try:
        WayModel.model_validate(way)
    except ValidationError:
        logger.warning('state.json way unusable, dropping it')
        return False
    return True


def repaired_pin(*, pin: object) -> dict[str, object] | None:
    """
    Repair one stored pin's windows, nickname and route filter.

    Parameters
    ----------
    pin : object
        The stored pin.

    Returns
    -------
    dict[str, object] | None
        The pin with bad windows dropped, a bad nickname reset to None
        (a long one is cleaned), a bad filter or never list reset to
        empty and unreadable ways dropped; None
        when the pin is still not valid.
    """
    if not isinstance(pin, dict):
        return None
    pin = repaired_ways(
        pin=repaired_windows(pin=pin), readable=readable_way,
    )
    pin = repaired_never(pin=pin)
    nickname = pin.get('nickname')
    fixed = {
        **pin,
        'nickname': (
            clean_nickname(text=nickname)
            if isinstance(nickname, str)
            else None
        ),
        'only': route_names(value=pin.get('only')),
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


def state_model_of(*, text: str) -> StateModel:
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
    migrated = repaired_auto(
        data=repaired_pins(
            data=repaired_settings(
                data=repaired_quota(data=migrate(data=data)),
            ),
        ),
    )
    return StateModel.model_validate(migrated)


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
            model = state_model_of(text=state_text(path=self.path))
        except (OSError, UnicodeDecodeError, ValueError, RecursionError):
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
