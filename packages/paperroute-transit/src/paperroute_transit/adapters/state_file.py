"""The board's section of the state file: the pins, view and settings."""

import logging
from collections.abc import Collection
from datetime import date, tzinfo

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
)

from paperroute_device.adapters.state_file import repaired_settings
from paperroute_transit.adapters.state_auto import (
    EMPTY_AUTO,
    AutoWindowModel,
    OccurrenceModel,
    occurrence_from_model,
    occurrence_to_model,
    repaired_windows,
)
from paperroute_transit.adapters.state_auto import (
    window_from_model as auto_window_from_model,
)
from paperroute_transit.adapters.state_auto import (
    window_to_model as auto_window_to_model,
)
from paperroute_transit.adapters.state_change import (
    ALLOW_MAX,
    ALLOW_MIN,
    repaired_never,
    repaired_ways,
    route_names,
)
from paperroute_transit.adapters.state_migrations import BOARD_SCHEMA
from paperroute_transit.adapters.state_settings import (
    BoardSettingsModel,
    board_settings_from_model,
    board_settings_to_model,
)
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

logger = logging.getLogger(__name__)



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


def from_model(*, model: BoardSectionModel, city: City) -> Saved:
    """
    Convert a stored board section back, repairing what can be repaired.

    Parameters
    ----------
    model : BoardSectionModel
        The stored section.
    city : City
        The city: its time zone and default pin.

    Returns
    -------
    Saved
        The board's state, with the city's default device settings. The
        current index is clamped into range, and no pins with no
        unpinned place gives the city's default pin.
    """
    pins = tuple(pin_from_model(model=pin) for pin in model.pins)
    unpinned = (
        None
        if model.unpinned is None
        else pin_from_model(model=model.unpinned)
    )
    if not pins and unpinned is None:
        unpinned = city.default_pin
    defaults = default_saved(city=city).settings
    return Saved(
        pins=pins,
        current=min(model.current, max(len(pins) - 1, 0)),
        unpinned=unpinned,
        view=model.view,
        settings=Settings(
            device=defaults.device,
            board=board_settings_from_model(model=model.settings),
        ),
        quota=Quota(day=model.quota.day, calls=model.quota.calls),
        auto=auto_from_model(model=model.auto, zone=city.zone),
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
