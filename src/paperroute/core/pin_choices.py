"""What a pin's editor accepts."""

from dataclasses import dataclass
from typing import Final

from paperroute.core.chooser import KIND_LABELS, KIND_ORDER, Choice
from paperroute_transit.core.model import Pin, RouteKind, StopPlace

NICKNAME_MAX: Final[int] = 24
KIND_NAMES: Final[dict[RouteKind, str]] = {
    kind: {**KIND_LABELS, RouteKind.REPLACEMENT: 'Replacement buses'}[kind]
    for kind in (
        RouteKind.BUS,
        RouteKind.REPLACEMENT,
        *(kind for kind in KIND_ORDER if kind is not RouteKind.BUS),
    )
}


@dataclass(frozen=True)
class ChangeStop:
    """
    A stop a connection can change to, with the walk to it.

    Attributes
    ----------
    place : StopPlace
        The stop or station.
    metres : int
        Straight-line walk to it, rounded up to the next 10 m.
    """

    place: StopPlace
    metres: int


@dataclass(frozen=True)
class StopMatch:
    """
    A stop that a search found.

    Attributes
    ----------
    place : StopPlace
        The stop or station.
    kinds : tuple[RouteKind, ...]
        The kinds of route that serve it, in `KIND_NAMES` order.
    routes : tuple[str, ...]
        Some of the route numbers that serve it.
    """

    place: StopPlace
    kinds: tuple[RouteKind, ...]
    routes: tuple[str, ...]


@dataclass(frozen=True)
class PinDraft:
    """
    The choices made so far in a pin's editor.

    Attributes
    ----------
    stop : str
        From stop or station ID; '' for none.
    kind : RouteKind | None
        Kind of route, or None for none yet.
    route : str
        Route number of the chosen line or route; '' for none.
    to : str
        Destination stop or station ID; '' for none.
    only : tuple[str, ...]
        Route numbers the pin's departures are limited to.
    nickname : str | None
        Name typed for the pin, or None.
    ref : str | None
        Ref of the pin being edited, or None for a new pin.
    """

    stop: str = ''
    kind: RouteKind | None = None
    route: str = ''
    to: str = ''
    only: tuple[str, ...] = ()
    nickname: str | None = None
    ref: str | None = None


@dataclass(frozen=True)
class PinEditor:
    """
    What a pin's editor shows.

    Attributes
    ----------
    draft : PinDraft
        The choices that are offered, with the nickname and ref.
    origin : StopPlace | None
        The From place; None until a stop is chosen.
    kinds : tuple[RouteKind, ...]
        Kinds offered at the From.
    lines : tuple[Choice, ...]
        Lines or routes offered for the kind.
    destinations : tuple[StopPlace, ...]
        Places offered as the To.
    filters : tuple[str, ...]
        Route numbers offered for the route filter.
    nearby : tuple[StopPlace, ...]
        Stops near a missing pin's old place.
    pin : Pin | None
        The pin the choices make; None until stop, kind and route are
        chosen.
    """

    draft: PinDraft
    origin: StopPlace | None
    kinds: tuple[RouteKind, ...]
    lines: tuple[Choice, ...]
    destinations: tuple[StopPlace, ...]
    filters: tuple[str, ...]
    nearby: tuple[StopPlace, ...]
    pin: Pin | None


def clean_nickname(*, text: str) -> str | None:
    """
    Tidy a nickname typed by the owner.

    Parameters
    ----------
    text : str
        The typed text.

    Returns
    -------
    str | None
        The text with whitespace collapsed and trimmed, cut to
        `NICKNAME_MAX` characters; None when nothing is left.
    """
    return ' '.join(text.split())[:NICKNAME_MAX].strip() or None
