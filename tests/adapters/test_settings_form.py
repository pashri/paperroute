"""Tests for the Settings form's fields."""

from dataclasses import replace
from datetime import time
from typing import Final

import pytest

from paperroute.adapters.settings_form import (
    BadInput,
    UnknownValue,
    changed_from,
    form_values,
    page_text,
)
from paperroute.adapters.web_common import asset
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


MAP_VALUES: Final[tuple[str, ...]] = (
    'changes', 'whole', 'first_leg', 'first', 'start',
)


def test_connection_map_five_choices() -> None:
    """Each connection map choice is read and checks its own radio."""
    template = ''.join(
        f'<{tag}{{{{connection_map:{value}}}}}>'
        for tag, value in zip('abcde', MAP_VALUES, strict=True)
    )
    for tag, value in zip('abcde', MAP_VALUES, strict=True):
        expected = ConnectionMap(value)
        assert changed_from(fields={'connection_map': value}) == (
            SettingsChanged(connection_map=expected)
        )
        text = page_text(
            template=template,
            values=form_values(settings=Settings(connection_map=expected)),
            error=None,
        )
        assert text.count(' checked') == 1
        assert f'<{tag} checked>' in text


def test_unknown_value_keeps_setting() -> None:
    """An unknown choice is refused, so the stored setting stands."""
    with pytest.raises(UnknownValue):
        changed_from(fields={'connection_map': 'change'})
    assert changed_from(fields={}).connection_map is None


MAP_HINTS: Final[tuple[tuple[str, str], ...]] = (
    (
        'Changes',
        'Zoomed in on each place you change, showing the services that '
        'meet there.',
    ),
    ('Whole trip', 'Everything from your stop to your destination.'),
    (
        'First leg',
        'Your first bus or train from your stop to the first change, with '
        'the vehicle on its way.',
    ),
    ('First change only', 'Zoomed in on the first place you change.'),
    (
        'Start only',
        'Zoomed in on your stop, with the vehicles coming towards it.',
    ),
)


def test_settings_page_offers_five_map_choices() -> None:
    """The page lists the five radios in order, each with its hint."""
    page = ' '.join(asset(name='settings.html').split())
    positions = []
    for (label, hint), value in zip(MAP_HINTS, MAP_VALUES, strict=True):
        radio = (
            f'<input type="radio" name="connection_map" value="{value}"'
        )
        positions += [page.index(radio), page.index(label), page.index(hint)]
    assert positions == sorted(positions)


def test_route_pin_switch_dimmed_by_css() -> None:
    """The route pin switch is dimmed while the school switch is off."""
    css = ' '.join(asset(name='live.css').split())
    assert (
        ':has(input[name="skip_school_buses"]:not(:checked)) '
        '.switch:has(input[name="skip_school_on_route_pins"])'
    ) in css


def test_route_pin_school_switch_read_and_shown() -> None:
    """The second school switch posts as on or off and is drawn unchecked."""
    assert changed_from(fields={'skip_school_on_route_pins': 'on'}) == (
        SettingsChanged(skip_school_on_route_pins=True)
    )
    values = form_values(settings=Settings())
    assert values['skip_school_on_route_pins'] == 'off'
    page = asset(name='settings.html')
    assert 'name="skip_school_on_route_pins"' in page
    assert 'Also on pins without a To' in page


def test_school_switch_read_and_shown() -> None:
    """The school switch posts as on or off and is drawn checked."""
    assert changed_from(fields={'skip_school_buses': 'off'}) == (
        SettingsChanged(skip_school_buses=False)
    )
    assert form_values(settings=Settings())['skip_school_buses'] == 'on'
    page = asset(name='settings.html')
    assert 'name="skip_school_buses"' in page
    assert 'Leave out school buses' in page
