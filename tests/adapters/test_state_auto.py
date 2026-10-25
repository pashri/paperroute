"""Tests for the stored form of auto windows."""

import logging

import pytest

from paperroute.adapters.state_auto import add_auto, repaired_windows

GOOD: dict[str, object] = {
    'days': ['mon', 'fri'], 'start': '22:00', 'end': '01:00',
}


def test_add_auto_gives_every_pin_windows() -> None:
    """The v7 to v8 step adds empty windows and an empty auto block."""
    data = add_auto({'pins': [{'kind': None}, 5], 'unpinned': {}})
    assert data['pins'] == [{'kind': None, 'windows': []}, 5]
    assert data['unpinned'] == {'windows': []}
    assert data['auto'] == {
        'occurrence': None, 'before': None, 'overridden_at': None,
    }


@pytest.mark.parametrize(
    'bad',
    [
        {'days': [], 'start': '07:00', 'end': '08:00'},
        {'days': ['mon', 'mon'], 'start': '07:00', 'end': '08:00'},
        {'days': ['mon'], 'start': '7:00', 'end': '08:00'},
        {'days': ['mon'], 'start': '07:00', 'end': '07:00'},
        {'days': ['mon'], 'start': '07:00', 'end': '08:00', 'x': 1},
        'window',
    ],
)
def test_bad_window_dropped(
    bad: object, caplog: pytest.LogCaptureFixture
) -> None:
    """Each unreadable window is dropped with a warning."""
    pin = repaired_windows(pin={'kind': None, 'windows': [GOOD, bad]})
    assert pin == {'kind': None, 'windows': [GOOD]}
    assert [record.levelno for record in caplog.records] == [
        logging.WARNING,
    ]


def test_windows_not_a_list_become_empty() -> None:
    """Windows that are not a list, or are missing, become empty."""
    assert repaired_windows(pin={'windows': 'x'}) == {'windows': []}
    assert repaired_windows(pin={}) == {'windows': []}
