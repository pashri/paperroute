"""Tests for the auto window rows of the pin editor."""

import re
from datetime import time
from typing import Final

from multidict import MultiDict

from paperroute.adapters.pin_windows import (
    NO_DAY,
    NO_TIME,
    SAME_TIME,
    WindowRow,
    rows_of,
    windows_fieldset,
    windows_of,
)
from paperroute_transit.core.model import AutoWindow

ROW: Final[WindowRow] = WindowRow(
    days=frozenset({0, 4}), start='07:30', end='09:00',
)


def fields(*pairs: tuple[str, str]) -> MultiDict[str]:
    """
    Make form fields from name and value pairs.

    Parameters
    ----------
    *pairs : tuple[str, str]
        Names and values, repeated names allowed.

    Returns
    -------
    MultiDict[str]
        The fields.
    """
    return MultiDict(pairs)


def test_rows_round_trip() -> None:
    """A row's fields read back as typed and make its window."""
    rows = rows_of(fields=fields(
        ('wn', '1'), ('w0_day', 'mon'), ('w0_day', 'fri'),
        ('w0_from', '07:30'), ('w0_to', '09:00'),
    ))
    assert rows == (ROW,)
    assert windows_of(rows=rows) == (
        AutoWindow(
            days=frozenset({0, 4}), start=time(7, 30), end=time(9),
        ),
    )


def test_add_and_remove_rows() -> None:
    """Add window adds a blank row; Remove drops its row."""
    assert rows_of(fields=fields(('add_window', '1'))) == (WindowRow(),)
    rows = rows_of(fields=fields(
        ('wn', '2'), ('w0_day', 'mon'), ('w1_day', 'tue'),
        ('remove_window', '0'),
    ))
    assert rows == (WindowRow(days=frozenset({1})),)


def test_rows_ignore_bad_fields() -> None:
    """A bad count, day or index is ignored, and the count is capped."""
    assert rows_of(fields=fields(('wn', 'x'))) == ()
    assert len(rows_of(fields=fields(('wn', '99')))) == 20
    assert rows_of(fields=fields(
        ('wn', '1'), ('w0_day', 'funday'), ('remove_window', '5'),
    )) == (WindowRow(),)


def test_refusals() -> None:
    """Each bad row gives its message; a blank row is dropped."""
    assert windows_of(rows=(
        WindowRow(days=frozenset({0}), start='07:00', end='07:00'),
    )) == SAME_TIME
    assert windows_of(
        rows=(WindowRow(start='07:00', end='08:00'),),
    ) == NO_DAY
    assert windows_of(
        rows=(WindowRow(days=frozenset({0}), start='07:00'),),
    ) == NO_TIME
    assert windows_of(
        rows=(WindowRow(days=frozenset({0}), start='7', end='08:00'),),
    ) == NO_TIME
    assert windows_of(rows=(WindowRow(), WindowRow())) == ()
    assert SAME_TIME == "A window can't start and end at the same time."
    assert NO_DAY == 'Tick at least one day for each window.'
    assert NO_TIME == 'Give each window a from and a to time.'


def test_overnight_allowed() -> None:
    """A window may run past midnight."""
    row = WindowRow(days=frozenset({5}), start='22:00', end='01:00')
    assert windows_of(rows=(row,)) == (
        AutoWindow(days=frozenset({5}), start=time(22), end=time(1)),
    )


def test_fieldset_markup() -> None:
    """One row draws its days, times, Remove, Add window and count."""
    text = windows_fieldset(rows=(ROW,))
    assert '<legend>Show automatically</legend>' in text
    assert '<legend>Window 1</legend>' in text
    assert re.findall(r'name="w0_day" value="(\w+)"', text) == [
        'mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun',
    ]
    assert 'value="mon" checked' in text
    assert 'value="tue" checked' not in text
    assert text.count('type="time"') == 2
    assert 'name="w0_from" value="07:30"' in text
    assert 'name="remove_window" value="0">Remove</button>' in text
    assert 'name="add_window" value="1">Add window</button>' in text
    assert '<input type="hidden" name="wn" value="1">' in text
    ids = re.findall(r'<input id="([\w-]+)"', text)
    assert ids == ['w0_from', 'w0_to']
    for found in ids:
        assert f'<label for="{found}">' in text
    assert text.count('<label><input type="checkbox"') == 7
    assert 'Not shown automatically.' not in text


def test_fieldset_without_rows() -> None:
    """With no rows, a line says so."""
    text = windows_fieldset(rows=())
    assert 'Not shown automatically.' in text
    assert 'name="wn" value="0"' in text


def test_fieldset_escapes_values() -> None:
    """A typed time is escaped."""
    text = windows_fieldset(rows=(WindowRow(start='"><b>'),))
    assert '"><b>' not in text


def test_rows_have_preview_output() -> None:
    """Each row has an empty live output; the fieldset carries the copy."""
    text = windows_fieldset(rows=(ROW, WindowRow()))
    assert text.count('<output class="window-preview" aria-live="polite">'
                      '</output>') == 2
    assert 'data-window-preview' in text
    assert f'data-no-day="{NO_DAY}"' in text
    assert f'data-no-time="{NO_TIME}"' in text
    assert 'data-same-time="A window can&#x27;t start' in text
