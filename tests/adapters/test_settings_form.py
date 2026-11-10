"""Tests for the Settings form's fields."""

from datetime import time
from typing import Final

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from paperroute_device.adapters import device_form
from paperroute_device.adapters.device_form import (
    DEVICE_RADIOS,
    DEVICE_SWITCHES,
    BadInput,
    UnknownValue,
    device_change_from,
    page_text,
)
from paperroute_device.adapters.web_common import asset
from paperroute_device.core.device_settings import (
    CLEAN_CHOICES,
    DeviceSettings,
    QuietMode,
    Window,
)
from paperroute_device.core.kit_events import DeviceChange
from paperroute_sydney.city import SYDNEY
from paperroute_transit.adapters.board_web import board_text
from paperroute_transit.adapters.settings_form import (
    BOARD_RADIOS,
    BOARD_SWITCHES,
    ERRORS,
    changed_from,
    form_values,
)
from paperroute_transit.core.messages import SettingsChanged
from paperroute_transit.core.saved import (
    BoardSettings,
    ConnectionMap,
    Lookahead,
    Rate,
    Settings,
    default_settings,
    with_changes,
)
from paperroute_transit.transit_app import QUIET_LABEL
from tests.adapters.transit_web import transit_display

Client = TestClient[web.Request, web.Application]

ODD = Settings(
    device=DeviceSettings(
        quiet_mode=QuietMode.APP,
        my_hours=Window(start=time(22, 45), end=time(4)),
        rotate=True,
        larger_text=True,
        inverted_lists=True,
        clean_after=60,
    ),
    board=BoardSettings(
        auto_rebuild=False,
        peak_rate=Rate.STEADY,
        offpeak_rate=Rate.FAST,
        peak_windows=(Window(start=time(7), end=time(8, 15)),),
        alert_lookahead=Lookahead.THREE_DAYS,
        connection_map=ConnectionMap.WHOLE,
    ),
)
PEAK = {
    'peak_1_start': '06:30',
    'peak_1_end': '10:00',
    'peak_2_start': '15:00',
    'peak_2_end': '19:00',
}


SWITCHES: Final[frozenset[str]] = frozenset(
    {*DEVICE_SWITCHES, *BOARD_SWITCHES},
)
RADIOS: Final[frozenset[str]] = frozenset({*DEVICE_RADIOS, *BOARD_RADIOS})
PAGE_ERRORS: Final[dict[str, str]] = {**device_form.ERRORS, **ERRORS}


def _board(*, fields: dict[str, str]) -> SettingsChanged:
    """
    Read the board's change from posted fields.

    Parameters
    ----------
    fields : dict[str, str]
        The posted fields, a board field among them.

    Returns
    -------
    SettingsChanged
        The board's change.
    """
    event = changed_from(fields=fields)
    assert event is not None
    return event


def _device(*, fields: dict[str, str]) -> DeviceChange:
    """
    Read the device's change from posted fields as Sydney's page does.

    Parameters
    ----------
    fields : dict[str, str]
        The posted fields.

    Returns
    -------
    DeviceChange
        The device's change.
    """
    return device_change_from(fields=fields, quiet_label=QUIET_LABEL)


def _applied(*, fields: dict[str, str]) -> Settings:
    """
    Apply both halves of a post to the defaults.

    Parameters
    ----------
    fields : dict[str, str]
        The posted fields.

    Returns
    -------
    Settings
        The defaults with each posted field set.
    """
    board = changed_from(fields=fields) or SettingsChanged()
    given = {
        name: value
        for name, value in {
            **vars(_device(fields=fields)),
            **vars(board),
        }.items()
        if value is not None
    }
    return with_changes(settings=default_settings(city=SYDNEY), changes=given)


def _marked(*, template: str, settings: Settings, error: str | None) -> str:
    """
    Fill a template's marks as Sydney's Settings page does.

    Parameters
    ----------
    template : str
        Text with `{{field}}` marks.
    settings : Settings
        The settings shown.
    error : str | None
        The section in error, if any.

    Returns
    -------
    str
        The filled text.
    """
    return page_text(
        template=template,
        values=form_values(settings=settings),
        error=error,
        switches=SWITCHES,
        radios=RADIOS,
        errors=PAGE_ERRORS,
    )


async def _drawn(*, settings: Settings) -> str:
    """
    Serve Sydney's Settings page with settings reported.

    Parameters
    ----------
    settings : Settings
        The settings the status reports.

    Returns
    -------
    str
        The page as served.
    """
    status: dict[str, object] = {'settings': form_values(settings=settings)}
    display = transit_display(status=lambda: status)
    client: Client = TestClient(TestServer(display.app()))
    await client.start_server()
    try:
        return await (await client.get('/settings')).text()
    finally:
        await client.close()


def _sections() -> str:
    """
    Read the board's sections of the Settings page, whitespace folded.

    Returns
    -------
    str
        The sections' text.
    """
    return ' '.join(board_text(name='settings_sections.html').split())


@pytest.mark.parametrize('settings', [default_settings(city=SYDNEY), ODD])
def test_form_values_round_trip(settings: Settings) -> None:
    """Posting the page's own values gives the same settings back."""
    fields = form_values(settings=settings)
    assert _applied(fields=fields) == settings


def test_form_values_spell_out_each_field() -> None:
    """The values are the strings the page posts."""
    values = form_values(settings=ODD)
    assert values['auto_rebuild'] == 'off'
    assert values['rotate'] == 'on'
    assert values['peak_rate'] == '60s'
    assert values['peak_1_start'] == '07:00'
    assert values['peak_2_start'] == ''
    assert values['quiet_mode'] == 'app'
    assert values['quiet_start'] == '22:45'


def test_lookahead_form_round_trip() -> None:
    """The lookahead is written as its value and read back alone."""
    values = form_values(
        settings=Settings(
            board=BoardSettings(alert_lookahead=Lookahead.THREE_DAYS),
        ),
    )
    assert values['alert_lookahead'] == '3d'
    event = _board(fields={'alert_lookahead': '3d'})
    assert event == SettingsChanged(alert_lookahead=Lookahead.THREE_DAYS)


def test_changed_from_reads_only_present_fields() -> None:
    """A one-field form changes one setting."""
    event = _board(fields={'peak_rate': '60s'})
    assert event == SettingsChanged(peak_rate=Rate.STEADY)


def test_changed_from_without_board_fields_is_none() -> None:
    """A post of device fields alone changes no board setting."""
    assert changed_from(fields={'rotate': 'on'}) is None


def test_empty_peak_slots_drop_windows() -> None:
    """Two empty slots save no windows."""
    fields = dict.fromkeys(PEAK, '')
    assert _board(fields=fields).peak_windows == ()


def test_one_filled_slot_keeps_one_window() -> None:
    """A slot left empty is dropped."""
    fields = {**PEAK, 'peak_1_start': '', 'peak_1_end': ''}
    event = _board(fields=fields)
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
        _device(fields={'quiet_start': bad, 'quiet_end': '05:30'})
    assert raised.value.section == 'quiet'


@pytest.mark.parametrize(
    'field', ['peak_rate', 'connection_map', 'auto_rebuild'],
)
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
    text = _marked(template=template, settings=ODD, error='peak')
    assert text.startswith('<option><option selected>')
    assert '<i checked>' in text
    assert 'value="22:45"' in text
    assert 'Peak hours not saved: each start must be before' in text
    assert 'Quiet hours not saved' not in text


MAP_VALUES: Final[tuple[str, ...]] = (
    'changes', 'whole', 'first_leg', 'start',
)


def test_connection_map_four_choices() -> None:
    """Each connection map choice is read and checks its own radio."""
    template = ''.join(
        f'<{tag}{{{{connection_map:{value}}}}}>'
        for tag, value in zip('abcd', MAP_VALUES, strict=True)
    )
    for tag, value in zip('abcd', MAP_VALUES, strict=True):
        expected = ConnectionMap(value)
        assert _board(fields={'connection_map': value}) == (
            SettingsChanged(connection_map=expected)
        )
        settings = Settings(board=BoardSettings(connection_map=expected))
        text = _marked(template=template, settings=settings, error=None)
        assert text.count(' checked') == 1
        assert f'<{tag} checked>' in text


def test_unknown_value_keeps_setting() -> None:
    """An unknown choice is refused, so the stored setting stands."""
    with pytest.raises(UnknownValue):
        changed_from(fields={'connection_map': 'change'})
    assert changed_from(fields={}) is None


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
    (
        'Start only',
        'Zoomed in on your stop, with the vehicles coming towards it.',
    ),
)


def test_settings_page_offers_four_map_choices() -> None:
    """The page lists the four radios in order, each with its hint."""
    page = _sections()
    positions = []
    for (label, hint), value in zip(MAP_HINTS, MAP_VALUES, strict=True):
        radio = (
            f'<input type="radio" name="connection_map" value="{value}"'
        )
        positions += [page.index(radio), page.index(label), page.index(hint)]
    assert positions == sorted(positions)
    assert 'First change only' not in page


def test_route_pin_switch_dimmed_by_css() -> None:
    """The route pin switch is dimmed while the school switch is off."""
    css = ' '.join(asset(name='palette.css').split())
    assert (
        ':has(input[name="skip_school_buses"]:not(:checked)) '
        '.switch:has(input[name="skip_school_on_route_pins"])'
    ) in css


def test_route_pin_school_switch_read_and_shown() -> None:
    """The second school switch posts as on or off and is drawn unchecked."""
    assert _board(fields={'skip_school_on_route_pins': 'on'}) == (
        SettingsChanged(skip_school_on_route_pins=True)
    )
    values = form_values(settings=default_settings(city=SYDNEY))
    assert values['skip_school_on_route_pins'] == 'off'
    page = _sections()
    assert 'name="skip_school_on_route_pins"' in page
    assert 'Also on pins without a To' in page


def test_school_switch_read_and_shown() -> None:
    """The school switch posts as on or off and is drawn checked."""
    assert _board(fields={'skip_school_buses': 'off'}) == (
        SettingsChanged(skip_school_buses=False)
    )
    assert form_values(
        settings=default_settings(city=SYDNEY),
    )['skip_school_buses'] == 'on'
    page = _sections()
    assert 'name="skip_school_buses"' in page
    assert 'Leave out school buses' in page


@pytest.mark.parametrize('count', CLEAN_CHOICES)
def test_clean_after_form_round_trip(count: int) -> None:
    """Each cleaning choice is written as its number and read back."""
    settings = Settings(device=DeviceSettings(clean_after=count))
    values = form_values(settings=settings)
    assert values['clean_after'] == str(count)
    event = _device(fields={'clean_after': str(count)})
    assert event == DeviceChange(clean_after=count)


@pytest.mark.parametrize('bad', ['45', '0', 'never', '', '030', ' 30'])
def test_clean_after_refuses_other_values(bad: str) -> None:
    """A count the page never offers is refused."""
    with pytest.raises(UnknownValue):
        _device(fields={'clean_after': bad})


async def test_clean_after_selects_its_option() -> None:
    """The page's cleaning choice is drawn with the saved count selected."""
    text = await _drawn(
        settings=Settings(device=DeviceSettings(clean_after=15)),
    )
    plain = ' '.join(text.split())
    assert 'Clean screen after' in plain
    assert '<select name="clean_after" aria-describedby="clean-hint">' in plain
    for count in CLEAN_CHOICES:
        assert f'>{count} changes</option>' in plain
    assert '<option value="15" selected>' in plain
    assert plain.count(' selected>') == 4


async def test_swap_knobs_switch_shown_checked() -> None:
    """The page draws the swap switch, checked when on."""
    text = await _drawn(
        settings=Settings(device=DeviceSettings(swap_knobs=True)),
    )
    plain = ' '.join(text.split())
    assert '<input type="hidden" name="swap_knobs" value="off">' in plain
    assert (
        '<input type="checkbox" role="switch" name="swap_knobs"'
        ' value="on" checked>'
    ) in plain
    assert 'Swap knobs' in plain
