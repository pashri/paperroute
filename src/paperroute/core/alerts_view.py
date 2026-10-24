"""The Alerts view: the shown pin's listed alerts and one alert's text."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from paperroute.core.alert_match import (
    Listed,
    time_line,
    when_text,
    window_key,
)
from paperroute.core.alert_model import Period
from paperroute.core.stop_view import PinTag, StopView

ALERT_ROWS: Final[int] = 6


@dataclass(frozen=True)
class AlertRow:
    """
    One alert in the list.

    Attributes
    ----------
    headline : str
        The alert's header.
    when : str
        Its time line.
    active : bool
        True when it applies now.
    """

    headline: str
    when: str
    active: bool


@dataclass(frozen=True)
class AlertText:
    """
    The selected alert's full text.

    Attributes
    ----------
    title : str
        The header.
    when : str
        The time line.
    body : str
        The description.
    also : tuple[str, ...]
        One line per later window.
    """

    title: str
    when: str
    body: str
    also: tuple[str, ...]


@dataclass(frozen=True)
class AlertsView:
    """
    The Alerts view.

    Attributes
    ----------
    origin : str
        The From stop's name.
    destination : str | None
        The To stop's name, or None.
    clock : str
        The clock text.
    tag : PinTag
        The pin tag.
    rows : tuple[AlertRow, ...]
        The window of at most `ALERT_ROWS` rows holding the selection.
    selected : int
        The selection's index in `rows`.
    text : AlertText
        The selected alert's text.
    page : int
        Page counter for its text; the renderer wraps it.
    """

    origin: str
    destination: str | None
    clock: str
    tag: PinTag
    rows: tuple[AlertRow, ...]
    selected: int
    text: AlertText
    page: int


def also_line(*, window: Period, now: datetime) -> str:
    """
    The line for a later window.

    Parameters
    ----------
    window : Period
        The window, which has a start.
    now : datetime
        Current time.

    Returns
    -------
    str
        'Also <start> to <end>', or 'Also from <start>, ongoing'.
    """
    start = '' if window.start is None else when_text(
        moment=window.start, now=now,
    )
    if window.end is None:
        return f'Also from {start}, ongoing'
    return f'Also {start} to {when_text(moment=window.end, now=now)}'


def alert_text(*, listed: Listed, now: datetime) -> AlertText:
    """
    The full text of a listed alert.

    Parameters
    ----------
    listed : Listed
        The alert.
    now : datetime
        Current time.

    Returns
    -------
    AlertText
        Header, time line, description and later windows.
    """
    alert = listed.matched.alert
    windows = sorted(alert.periods, key=window_key)
    later = windows[len(windows) - listed.timing.more:]
    return AlertText(
        title=alert.header,
        when=time_line(timing=listed.timing, now=now),
        body=alert.description,
        also=tuple(also_line(window=item, now=now) for item in later),
    )


def alert_row(*, listed: Listed, now: datetime) -> AlertRow:
    """
    The list row of an alert.

    Parameters
    ----------
    listed : Listed
        The alert.
    now : datetime
        Current time.

    Returns
    -------
    AlertRow
        Its row.
    """
    return AlertRow(
        headline=listed.matched.alert.header,
        when=time_line(timing=listed.timing, now=now),
        active=listed.timing.active,
    )


def selected_index(*, listed: Sequence[Listed], selected: str | None) -> int:
    """
    Find the selected alert in the list.

    Parameters
    ----------
    listed : Sequence[Listed]
        Listed alerts.
    selected : str | None
        Selected alert ID.

    Returns
    -------
    int
        Its index; 0 when it isn't listed.
    """
    ids = [item.matched.alert.alert_id for item in listed]
    return ids.index(selected) if selected in ids else 0


def build_alerts_view(
    *,
    listed: Sequence[Listed],
    selected: str | None,
    page: int,
    stop_view: StopView,
    now: datetime,
) -> AlertsView:
    """
    Build the Alerts view.

    Parameters
    ----------
    listed : Sequence[Listed]
        The shown pin's listed alerts, ranked; at least one.
    selected : str | None
        Selected alert ID; the first is selected when it isn't listed.
    page : int
        Page counter of the selected text.
    stop_view : StopView
        The Stop view, for the header's texts.
    now : datetime
        Current time.

    Returns
    -------
    AlertsView
        The view.
    """
    index = selected_index(listed=listed, selected=selected)
    first = max(0, index - ALERT_ROWS + 1)
    shown = listed[first:first + ALERT_ROWS]
    return AlertsView(
        origin=stop_view.origin,
        destination=stop_view.destination,
        clock=stop_view.clock,
        tag=stop_view.tag,
        rows=tuple(alert_row(listed=item, now=now) for item in shown),
        selected=index - first,
        text=alert_text(listed=listed[index], now=now),
        page=page,
    )
