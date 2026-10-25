"""Read and draw the auto window rows of the pin editor."""

import html
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import time
from typing import Final

from multidict import MultiMapping

from paperroute.adapters.settings_form import BadInput, clock_of, clock_text
from paperroute.core.model import DAY_NAMES, AutoWindow, Pin

MAX_ROWS: Final[int] = 20
NO_DAY: Final[str] = 'Tick at least one day for each window.'
NO_TIME: Final[str] = 'Give each window a from and a to time.'
SAME_TIME: Final[str] = "A window can't start and end at the same time."
NO_ROWS: Final[str] = """
        <p>Not shown automatically.</p>"""
DEFAULT_BUTTON: Final[str] = (
    '<button type="submit" class="default-submit" tabindex="-1" '
    'aria-hidden="true">'
    'Show choices</button>'
)
ROW: Final[str] = """
        <fieldset class="window">
          <legend>Window {number}</legend>
          <p class="days">{days}
          </p>
          <p class="times">
            <label for="w{i}_from">From</label>
            <input id="w{i}_from" type="time"
                   name="w{i}_from" value="{start}">
            <label for="w{i}_to">To</label>
            <input id="w{i}_to" type="time"
                   name="w{i}_to" value="{end}">
          </p>
          <button type="submit"
                  name="remove_window" value="{i}">Remove</button>
        </fieldset>"""
FIELDSET: Final[str] = """
      <fieldset class="windows">
        <legend>Show automatically</legend>{rows}
        <button type="submit"
                name="add_window" value="1">Add window</button>
        <input type="hidden" name="wn" value="{count}">
      </fieldset>"""


@dataclass(frozen=True)
class WindowRow:
    """
    One window row of the editor, as typed.

    Attributes
    ----------
    days : frozenset[int]
        Days ticked, 0 (Monday) to 6 (Sunday).
    start : str
        The From field's text.
    end : str
        The To field's text.
    """

    days: frozenset[int] = frozenset()
    start: str = ''
    end: str = ''


def count_of(*, text: str) -> int:
    """
    Read a row count.

    Parameters
    ----------
    text : str
        The `wn` field's text.

    Returns
    -------
    int
        The count, capped at `MAX_ROWS`; 0 when it is not a number.
    """
    return min(int(text), MAX_ROWS) if text.isdigit() else 0


def row_of(*, fields: MultiMapping[str], index: int) -> WindowRow:
    """
    Read one row from form fields.

    Parameters
    ----------
    fields : MultiMapping[str]
        The form's fields.
    index : int
        The row's index.

    Returns
    -------
    WindowRow
        The row; unknown day names are ignored.
    """
    return WindowRow(
        days=frozenset(
            DAY_NAMES.index(name)
            for name in fields.getall(f'w{index}_day', [])
            if name in DAY_NAMES
        ),
        start=fields.get(f'w{index}_from', ''),
        end=fields.get(f'w{index}_to', ''),
    )


def rows_of(*, fields: MultiMapping[str]) -> tuple[WindowRow, ...]:
    """
    Read the rows from form fields, then add or remove one.

    Parameters
    ----------
    fields : MultiMapping[str]
        The form's fields: `wn`, each row's `w{i}_day`, `w{i}_from` and
        `w{i}_to`, and `add_window` or `remove_window` when pressed.

    Returns
    -------
    tuple[WindowRow, ...]
        The rows, with a blank one added or the one named removed.
    """
    count = count_of(text=fields.get('wn', ''))
    rows = [row_of(fields=fields, index=index) for index in range(count)]
    if 'add_window' in fields and len(rows) < MAX_ROWS:
        rows.append(WindowRow())
    remove = fields.get('remove_window', '')
    if remove.isdigit() and int(remove) < len(rows):
        del rows[int(remove)]
    return tuple(rows)


def rows_from(*, pin: Pin) -> tuple[WindowRow, ...]:
    """
    Make the rows for a pin's windows.

    Parameters
    ----------
    pin : Pin
        The pin.

    Returns
    -------
    tuple[WindowRow, ...]
        One row per window.
    """
    return tuple(
        WindowRow(
            days=window.days,
            start=clock_text(moment=window.start),
            end=clock_text(moment=window.end),
        )
        for window in pin.windows
    )


def clock_or_none(*, text: str) -> time | None:
    """
    Read an `HH:MM` time, if it is one.

    Parameters
    ----------
    text : str
        The text.

    Returns
    -------
    time | None
        The time, or None when the text is not one.
    """
    try:
        return clock_of(text=text, section='windows')
    except BadInput:
        return None


def window_of(*, row: WindowRow) -> AutoWindow | str:
    """
    Make one window from a row.

    Parameters
    ----------
    row : WindowRow
        A row that is not blank.

    Returns
    -------
    AutoWindow | str
        The window, or the message saying why the row is refused.
    """
    if not row.days:
        return NO_DAY
    start = clock_or_none(text=row.start)
    end = clock_or_none(text=row.end)
    if start is None or end is None:
        return NO_TIME
    if start == end:
        return SAME_TIME
    return AutoWindow(days=row.days, start=start, end=end)


def windows_of(
    *, rows: Sequence[WindowRow],
) -> tuple[AutoWindow, ...] | str:
    """
    Make the windows from the rows.

    Parameters
    ----------
    rows : Sequence[WindowRow]
        The rows; fully blank ones are dropped.

    Returns
    -------
    tuple[AutoWindow, ...] | str
        The windows, or the first refusal message.
    """
    windows = []
    for row in rows:
        if row == WindowRow():
            continue
        window = window_of(row=row)
        if isinstance(window, str):
            return window
        windows.append(window)
    return tuple(windows)


def day_boxes(*, row: WindowRow, index: int) -> str:
    """
    Draw a row's seven day checkboxes.

    Parameters
    ----------
    row : WindowRow
        The row.
    index : int
        Its index.

    Returns
    -------
    str
        Labelled checkboxes, Monday first.
    """
    return ''.join(
        f'\n            <label><input type="checkbox" name="w{index}_day" '
        f'value="{name}"{" checked" if day in row.days else ""}> '
        f'{name.title()}</label>'
        for day, name in enumerate(DAY_NAMES)
    )


def windows_fieldset(*, rows: Sequence[WindowRow]) -> str:
    """
    Draw the Show automatically fieldset.

    Parameters
    ----------
    rows : Sequence[WindowRow]
        The rows.

    Returns
    -------
    str
        Escaped HTML: the rows, Add window and the row count.
    """
    drawn = ''.join(
        ROW.format(
            number=index + 1,
            i=index,
            days=day_boxes(row=row, index=index),
            start=html.escape(row.start),
            end=html.escape(row.end),
        )
        for index, row in enumerate(rows)
    )
    return FIELDSET.format(rows=drawn or NO_ROWS, count=len(rows))


def query_rows(
    *, fields: MultiMapping[str],
) -> tuple[WindowRow, ...] | None:
    """
    Read the rows from an editor's query, if it has them.

    Parameters
    ----------
    fields : MultiMapping[str]
        The query's fields.

    Returns
    -------
    tuple[WindowRow, ...] | None
        The rows, or None when the query has no `wn`.
    """
    return rows_of(fields=fields) if 'wn' in fields else None


def start_rows(
    *, rows: tuple[WindowRow, ...] | None, pin: Pin | None,
) -> tuple[WindowRow, ...]:
    """
    Choose the rows an editor starts with.

    Parameters
    ----------
    rows : tuple[WindowRow, ...] | None
        Rows from the form, or None when it had none.
    pin : Pin | None
        The pin being edited, or None for a new pin.

    Returns
    -------
    tuple[WindowRow, ...]
        The form's rows, else the pin's windows, else none.
    """
    if rows is not None:
        return rows
    return () if pin is None else rows_from(pin=pin)
