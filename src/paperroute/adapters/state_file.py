"""The pins and view, kept in a schema-versioned JSON file."""

import contextlib
import json
import logging
import time
from collections.abc import Collection
from datetime import date, tzinfo
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
from paperroute.adapters.state_migrations import (
    BOARD_SCHEMA,
    SCHEMA_VERSION,
    migrate,
)
from paperroute.adapters.state_settings import (
    BoardSettingsModel,
    DeviceSettingsModel,
    board_settings_from_model,
    board_settings_to_model,
    device_settings_from_model,
    device_settings_to_model,
    repaired_settings,
)
from paperroute_device.adapters.atomic_file import write_atomic
from paperroute_device.core.device_settings import DeviceSettings
from paperroute_device.core.sections import DEVICE_SCHEMA
from paperroute_transit.core.auto_model import AutoState, Before
from paperroute_transit.core.city import City
from paperroute_transit.core.model import (
    MAX_CHANGES,
    Change,
    Pin,
    StopPlace,
    Way,
)
from paperroute_transit.core.pin_choices import NICKNAME_MAX, clean_nickname
from paperroute_transit.core.saved import (
    LiveView,
    Quota,
    Saved,
    Settings,
    default_saved,
)
from paperroute_transit.ports import Loaded

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
    kind: str | None
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


class DeviceSectionModel(BaseModel):
    """What the device keeps for itself, as stored."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    schema_version: int
    settings: DeviceSettingsModel


class BoardSectionModel(BaseModel):
    """What the transit board keeps, as stored."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    schema_version: int
    pins: tuple[PinModel, ...]
    current: int = Field(ge=0)
    unpinned: PinModel | None
    view: LiveView
    settings: BoardSettingsModel
    quota: QuotaModel
    auto: AutoModel


class StateModel(BaseModel):
    """The whole file."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    schema_version: int
    device: DeviceSectionModel
    board: BoardSectionModel


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


def auto_from_model(*, model: AutoModel, zone: tzinfo) -> AutoState:
    """
    Convert a stored auto pin state back.

    Parameters
    ----------
    model : AutoModel
        The stored state.
    zone : tzinfo
        Local time zone.

    Returns
    -------
    AutoState
        The state, its times in local time.
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
            None if overridden_at is None else overridden_at.astimezone(zone)
        ),
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


def board_section(*, saved: Saved) -> BoardSectionModel:
    """
    Convert the board's part of the saved state to its stored section.

    Parameters
    ----------
    saved : Saved
        The pins and view.

    Returns
    -------
    BoardSectionModel
        The section at `BOARD_SCHEMA`.
    """
    unpinned = saved.unpinned
    return BoardSectionModel(
        schema_version=BOARD_SCHEMA,
        pins=tuple(pin_to_model(pin=pin) for pin in saved.pins),
        current=saved.current,
        unpinned=None if unpinned is None else pin_to_model(pin=unpinned),
        view=saved.view,
        settings=board_settings_to_model(settings=saved.settings.board),
        quota=QuotaModel(day=saved.quota.day, calls=saved.quota.calls),
        auto=auto_to_model(auto=saved.auto),
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
    return StateModel(
        schema_version=SCHEMA_VERSION,
        device=device_section(settings=saved.settings.device),
        board=board_section(saved=saved),
    )


def from_model(*, model: StateModel, city: City) -> Saved:
    """
    Convert a stored state back, repairing what can be repaired.

    Parameters
    ----------
    model : StateModel
        The stored state.
    city : City
        The city: its time zone and default pin.

    Returns
    -------
    Saved
        The state. The current index is clamped into range, and no pins
        with no unpinned place gives the city's default pin.
    """
    board = model.board
    pins = tuple(pin_from_model(model=pin) for pin in board.pins)
    unpinned = (
        None
        if board.unpinned is None
        else pin_from_model(model=board.unpinned)
    )
    if not pins and unpinned is None:
        unpinned = city.default_pin
    return Saved(
        pins=pins,
        current=min(board.current, max(len(pins) - 1, 0)),
        unpinned=unpinned,
        view=board.view,
        settings=Settings(
            device=device_settings_from_model(model=model.device.settings),
            board=board_settings_from_model(model=board.settings),
        ),
        quota=Quota(day=board.quota.day, calls=board.quota.calls),
        auto=auto_from_model(model=board.auto, zone=city.zone),
    )


def repaired_quota(*, data: dict[str, object]) -> dict[str, object]:
    """
    Reset a quota that cannot be read, so the pins are kept.

    Parameters
    ----------
    data : dict[str, object]
        The migrated board section.

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
        The migrated board section.

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


def usable_pin(*, pin: dict[str, object], mode_ids: Collection[str]) -> bool:
    """
    Say whether a repaired stored pin can be read.

    Parameters
    ----------
    pin : dict[str, object]
        The repaired pin.
    mode_ids : Collection[str]
        The city's mode ids.

    Returns
    -------
    bool
        True when it is a valid pin whose kind is None or one of the
        city's modes.
    """
    try:
        PinModel.model_validate(pin)
    except ValidationError:
        return False
    return known_kind(pin=pin, mode_ids=mode_ids)


def known_kind(*, pin: dict[str, object], mode_ids: Collection[str]) -> bool:
    """
    Say whether a stored pin's kind is one the city knows.

    Parameters
    ----------
    pin : dict[str, object]
        The stored pin.
    mode_ids : Collection[str]
        The city's mode ids.

    Returns
    -------
    bool
        True when its kind is None or one of the city's modes.
    """
    kind = pin.get('kind')
    return kind is None or kind in mode_ids


def repaired_pin(
    *, pin: object, mode_ids: Collection[str],
) -> dict[str, object] | None:
    """
    Repair one stored pin's windows, nickname and route filter.

    Parameters
    ----------
    pin : object
        The stored pin.
    mode_ids : Collection[str]
        The city's mode ids; a pin of any other kind is dropped.

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
    if not usable_pin(pin=fixed, mode_ids=mode_ids):
        logger.warning('state.json pin unusable, dropping it')
        return None
    return fixed


def repaired_pins(
    *, data: dict[str, object], mode_ids: Collection[str],
) -> dict[str, object]:
    """
    Repair each pin, dropping only the ones that cannot be read.

    Parameters
    ----------
    data : dict[str, object]
        The migrated board section.
    mode_ids : Collection[str]
        The city's mode ids; a pin, unpinned place or before place of
        any other kind is dropped.

    Returns
    -------
    dict[str, object]
        The section with each pin repaired and unusable pins dropped,
        the unpinned place None when it is unusable, and the auto before
        place None when its kind is unknown. A section whose pins are
        not a list is left for validation to fail.
    """
    pins = data.get('pins')
    if not isinstance(pins, list):
        return data
    kept = [repaired_pin(pin=pin, mode_ids=mode_ids) for pin in pins]
    unpinned = data.get('unpinned')
    return {
        **data,
        'pins': [pin for pin in kept if pin is not None],
        'unpinned': (
            None
            if unpinned is None
            else repaired_pin(pin=unpinned, mode_ids=mode_ids)
        ),
        'auto': known_before(auto=data.get('auto'), mode_ids=mode_ids),
    }


def known_before(*, auto: object, mode_ids: Collection[str]) -> object:
    """
    Forget the place kept by the auto pin state when its kind is unknown.

    Parameters
    ----------
    auto : object
        The stored auto block.
    mode_ids : Collection[str]
        The city's mode ids.

    Returns
    -------
    object
        The block with `before` None when its place is of a kind the
        city does not know; anything else is left for validation.
    """
    if not isinstance(auto, dict):
        return auto
    before = auto.get('before')
    place = before.get('place') if isinstance(before, dict) else None
    if not isinstance(place, dict) or known_kind(
        pin=place, mode_ids=mode_ids,
    ):
        return auto
    logger.warning('state.json auto before place unusable, dropping it')
    return {**auto, 'before': None}


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


def state_model_of(*, text: str, city: City) -> StateModel:
    """
    Parse the file's text, migrating and repairing it first.

    Parameters
    ----------
    text : str
        The file's contents.
    city : City
        The city: pins of a kind it does not declare are dropped, and
        missing or reset settings take its defaults.

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
    migrated = migrate(data=data, city=city)
    return StateModel.model_validate({
        **migrated,
        'device': repaired_device(section=migrated.get('device'), city=city),
        'board': repaired_board(section=migrated.get('board'), city=city),
    })


def repaired_device(*, section: object, city: City) -> dict[str, object]:
    """
    Repair the migrated device section.

    Parameters
    ----------
    section : object
        The migrated section, as stored.
    city : City
        The city, whose defaults reset settings take.

    Returns
    -------
    dict[str, object]
        The section with bad settings reset; all defaults when it is
        missing or not an object.
    """
    defaults = device_section(
        settings=default_saved(city=city).settings.device,
    ).model_dump(mode='json')
    if not isinstance(section, dict):
        logger.warning('state.json device section unusable, resetting it')
        return defaults
    return repaired_settings(
        data=section,
        model=DeviceSettingsModel,
        defaults=defaults['settings'],
    )


def repaired_board(*, section: object, city: City) -> dict[str, object]:
    """
    Repair the migrated board section.

    Parameters
    ----------
    section : object
        The migrated section, as stored.
    city : City
        The city: pins of a kind it does not declare are dropped, and
        reset settings and a reset section take its defaults.

    Returns
    -------
    dict[str, object]
        The section with its quota, settings, pins and auto block
        repaired; all defaults when it is missing or not an object.
    """
    defaults = board_section(
        saved=default_saved(city=city),
    ).model_dump(mode='json')
    if not isinstance(section, dict):
        logger.warning('state.json board section unusable, resetting it')
        return defaults
    return repaired_auto(
        data=repaired_pins(
            data=repaired_settings(
                data=repaired_quota(data=section),
                model=BoardSettingsModel,
                defaults=defaults['settings'],
            ),
            mode_ids=city.mode_ids,
        ),
    )


class JsonStateRepository:
    """
    The saved state in a JSON file.

    Parameters
    ----------
    path : Path
        The file.
    city : City
        The city the state is kept for.
    """

    def __init__(self, *, path: Path, city: City) -> None:
        self.path = path
        self.city = city

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
            return Loaded(saved=default_saved(city=self.city), stored=False)
        try:
            model = state_model_of(
                text=state_text(path=self.path), city=self.city,
            )
        except (OSError, UnicodeDecodeError, ValueError, RecursionError):
            return self.defaults_after_error()
        saved = from_model(model=model, city=self.city)
        return Loaded(saved=saved, stored=True)

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
        return Loaded(saved=default_saved(city=self.city), stored=False)

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
