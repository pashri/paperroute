"""Tests for the Settings form's fields."""

from dataclasses import replace
from datetime import time

import pytest

from paperroute.adapters.settings_form import (
    BadInput,
    UnknownValue,
    changed_from,
    form_values,
    page_text,
)
from paperroute.core.messages import SettingsChanged
from paperroute.core.saved import (
    ConnectionMap,
    Lookahead,
    QuietMode,
    Rate,
    Settings,
    Window,
)

ODD = Settings(
    auto_rebuild=False,
    peak_rate=Rate.STEADY,
    offpeak_rate=Rate.FAST,
    peak_windows=(Window(start=time(7), end=time(8, 15)),),
    quiet_mode=QuietMode.TIMETABLE,
    my_hours=Window(start=time(22, 45), end=time(4)),
    rotate=True,
    larger_text=True,
    inverted_lists=True,
    alert_lookahead=Lookahead.THREE_DAYS,
    connection_map=ConnectionMap.WHOLE,
)
PEAK = {
    'peak_1_start': '06:30',
    'peak_1_end': '10:00',
    'peak_2_start': '15:00',
    'peak_2_end': '19:00',
}


def _applied(*, event: SettingsChanged) -> Settings:
    """Apply an event's set fields to the defaults."""
    given = {k: v for k, v in vars(event).items() if v is not None}
    return replace(Settings(), **given)


@pytest.mark.parametrize('settings', [Settings(), ODD])
def test_form_values_round_trip(settings: Settings) -> None:
    """Posting the page's own values gives the same settings back."""
    fields = form_values(settings=settings)
    assert _applied(event=changed_from(fields=fields)) == settings


def test_form_values_spell_out_each_field() -> None:
    """The values are the strings the page posts."""
    values = form_values(settings=ODD)
    assert values['auto_rebuild'] == 'off'
    assert values['rotate'] == 'on'
    assert values['peak_rate'] == '60s'
    assert values['peak_1_start'] == '07:00'
    assert values['peak_2_start'] == ''
    assert values['quiet_mode'] == 'timetable'
    assert values['quiet_start'] == '22:45'


def test_lookahead_form_round_trip() -> None:
    """The lookahead is written as its value and read back alone."""
    values = form_values(
        settings=Settings(alert_lookahead=Lookahead.THREE_DAYS),
    )
    assert values['alert_lookahead'] == '3d'
    event = changed_from(fields={'alert_lookahead': '3d'})
    assert event == SettingsChanged(alert_lookahead=Lookahead.THREE_DAYS)


def test_changed_from_reads_only_present_fields() -> None:
    """A one-field form changes one setting."""
    event = changed_from(fields={'rotate': 'on'})
    assert event == SettingsChanged(rotate=True)


def test_empty_peak_slots_drop_windows() -> None:
    """Two empty slots save no windows."""
    fields = dict.fromkeys(PEAK, '')
    assert changed_from(fields=fields).peak_windows == ()


def test_one_filled_slot_keeps_one_window() -> None:
    """A slot left empty is dropped."""
    fields = {**PEAK, 'peak_1_start': '', 'peak_1_end': ''}
    event = changed_from(fields=fields)
    assert event.peak_windows == (
        Window(start=time(15), end=time(19)),
    )


def test_half_empty_slot_is_bad() -> None:
    """A slot with one time is refused."""
    with pytest.raises(BadInput) as raised:
        changed_from(fields={**PEAK, 'peak_2_end': ''})
    assert raised.value.section == 'peak'


@pytest.mark.parametrize('end', ['06:30', '06:00'])
def test_peak_start_after_end_is_bad(end: str) -> None:
    """A window must start before it ends."""
    with pytest.raises(BadInput):
        changed_from(fields={**PEAK, 'peak_1_end': end})


def test_overlapping_peak_windows_are_bad() -> None:
    """Windows may not overlap."""
    with pytest.raises(BadInput):
        changed_from(fields={**PEAK, 'peak_2_start': '09:00'})


@pytest.mark.parametrize('bad', ['7:3', '24:00', 'soon'])
def test_bad_time_is_bad(bad: str) -> None:
    """Times must be HH:MM."""
    with pytest.raises(BadInput):
        changed_from(fields={**PEAK, 'peak_1_start': bad})
    with pytest.raises(BadInput) as raised:
        changed_from(fields={'quiet_start': bad, 'quiet_end': '05:30'})
    assert raised.value.section == 'quiet'


def test_quiet_wrap_is_fine() -> None:
    """Quiet hours may cross midnight."""
    event = changed_from(
        fields={'quiet_start': '23:00', 'quiet_end': '05:30'},
    )
    assert event.my_hours == Window(start=time(23), end=time(5, 30))


@pytest.mark.parametrize('field', ['peak_rate', 'quiet_mode', 'rotate'])
def test_unknown_values_are_unknown(field: str) -> None:
    """A value the page never sends is refused."""
    with pytest.raises(UnknownValue):
        changed_from(fields={field: 'sideways'})


def test_page_text_fills_marks() -> None:
    """Selected, checked and value marks are filled; an error shows."""
    template = (
        '<option{{peak_rate:30s}}><option{{peak_rate:60s}}>'
        '<i{{rotate}}><b value="{{quiet_start}}">{{error_peak}}{{error_quiet}}'
    )
    text = page_text(
        template=template,
        values=form_values(settings=ODD),
        error='peak',
    )
    assert text.startswith('<option><option selected>')
    assert '<i checked>' in text
    assert 'value="22:45"' in text
    assert 'Peak hours not saved: each start must be before' in text
    assert 'Quiet hours not saved' not in text


def test_connection_map_posted() -> None:
    """The connection map choice is read; an unknown one is refused."""
    assert changed_from(fields={'connection_map': 'whole'}) == (
        SettingsChanged(connection_map=ConnectionMap.WHOLE)
    )
    with pytest.raises(UnknownValue):
        changed_from(fields={'connection_map': 'both'})


def test_connection_map_selected() -> None:
    """The stored connection map choice is the selected option."""
    text = page_text(
        template=(
            '<option{{connection_map:change}}>'
            '<option{{connection_map:whole}}>'
        ),
        values=form_values(
            settings=Settings(connection_map=ConnectionMap.WHOLE),
        ),
        error=None,
    )
    assert text == '<option><option selected>'
