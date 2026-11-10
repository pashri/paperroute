"""Tests for the Settings page's device fields."""

from datetime import time
from typing import Final

import pytest

from paperroute_device.adapters.device_form import (
    DEVICE_RADIOS,
    DEVICE_SWITCHES,
    BadInput,
    UnknownValue,
    device_change_from,
    device_values,
    error_line,
    page_text,
)
from paperroute_device.core.device_settings import (
    CLEAN_CHOICES,
    DeviceSettings,
    QuietMode,
    Window,
)
from paperroute_device.core.kit_events import DeviceChange

ODD: Final[DeviceSettings] = DeviceSettings(
    quiet_mode=QuietMode.APP,
    my_hours=Window(start=time(22, 45), end=time(4)),
    rotate=True,
    larger_text=True,
    inverted_lists=False,
    clean_after=120,
    swap_knobs=True,
)
LABEL: Final[str] = 'Follow the app'


def test_device_values_write_each_field() -> None:
    """Every device field is written as the page's controls hold it."""
    assert device_values(settings=ODD) == {
        'rotate': 'on',
        'larger_text': 'on',
        'inverted_lists': 'off',
        'swap_knobs': 'on',
        'quiet_mode': 'app',
        'clean_after': '120',
        'quiet_start': '22:45',
        'quiet_end': '04:00',
    }


@pytest.mark.parametrize('settings', [ODD, DeviceSettings()])
def test_device_values_round_trip(settings: DeviceSettings) -> None:
    """The page's values post back as a change to the same settings."""
    change = device_change_from(
        fields=device_values(settings=settings), quiet_label=LABEL,
    )
    assert change == DeviceChange(
        quiet_mode=settings.quiet_mode,
        my_hours=settings.my_hours,
        rotate=settings.rotate,
        larger_text=settings.larger_text,
        inverted_lists=settings.inverted_lists,
        clean_after=settings.clean_after,
        swap_knobs=settings.swap_knobs,
    )


def test_absent_fields_change_nothing() -> None:
    """A post with no device fields changes no device setting."""
    assert device_change_from(
        fields={'peak_rate': 'x'}, quiet_label=LABEL,
    ) == DeviceChange()


def test_app_mode_refused_without_label() -> None:
    """An app with no quiet mode of its own refuses a posted `app`."""
    with pytest.raises(UnknownValue):
        device_change_from(fields={'quiet_mode': 'app'}, quiet_label=None)


def test_my_hours_allowed_without_label() -> None:
    """My hours may be chosen when the app has no quiet mode."""
    change = device_change_from(
        fields={'quiet_mode': 'my_hours'}, quiet_label=None,
    )
    assert change.quiet_mode is QuietMode.MY_HOURS


@pytest.mark.parametrize(
    'fields',
    [
        {'rotate': 'yes'},
        {'quiet_mode': 'timetable'},
        {'clean_after': '7'},
    ],
)
def test_unknown_values_are_refused(fields: dict[str, str]) -> None:
    """A switch, mode or count the page never sends is refused."""
    with pytest.raises(UnknownValue):
        device_change_from(fields=fields, quiet_label=LABEL)


def test_every_offered_count_is_read() -> None:
    """Each cleaning count the page offers is read back."""
    for count in CLEAN_CHOICES:
        change = device_change_from(
            fields={'clean_after': str(count)}, quiet_label=LABEL,
        )
        assert change.clean_after == count


@pytest.mark.parametrize(
    'fields',
    [
        {'quiet_start': '22:00'},
        {'quiet_start': '25:00', 'quiet_end': '06:00'},
        {'quiet_start': '2200', 'quiet_end': '06:00'},
    ],
)
def test_bad_quiet_hours_blame_quiet(fields: dict[str, str]) -> None:
    """Unusable quiet hours are refused for the quiet section."""
    with pytest.raises(BadInput) as raised:
        device_change_from(fields=fields, quiet_label=LABEL)
    assert raised.value.section == 'quiet'


def test_page_text_marks_switches_and_radios() -> None:
    """Switches, radios, lists and values each fill their own way."""
    template = (
        '{{rotate}}|{{skip}}|{{quiet_mode:app}}|{{quiet_mode:my_hours}}|'
        '{{map:big}}|{{clean_after:120}}|{{clean_after:15}}|{{name}}|'
        '{{error_quiet}}'
    )
    text = page_text(
        template=template,
        values={
            'rotate': 'on',
            'skip': 'on',
            'quiet_mode': 'app',
            'map': 'big',
            'clean_after': '120',
            'name': '<b>',
        },
        error=None,
        switches=frozenset({*DEVICE_SWITCHES, 'skip'}),
        radios=frozenset({*DEVICE_RADIOS, 'map'}),
    )
    assert text == (
        ' checked| checked| checked|| checked| selected||&lt;b&gt;|'
    )


def test_page_text_shows_the_error() -> None:
    """The section in error shows its message."""
    text = page_text(
        template='{{error_quiet}}',
        values={},
        error='quiet',
        switches=frozenset(DEVICE_SWITCHES),
        radios=frozenset(DEVICE_RADIOS),
    )
    assert text == error_line(shown=True, section='quiet')
    assert 'Quiet hours not saved' in text


def test_error_line_is_empty_when_not_shown() -> None:
    """A section without an error shows nothing."""
    assert not error_line(shown=False, section='quiet')


def test_page_text_shows_an_apps_error() -> None:
    """An app's section shows its own message when it is in error."""
    text = page_text(
        template='{{error_peak}}{{error_quiet}}',
        values={},
        error='peak',
        switches=frozenset(DEVICE_SWITCHES),
        radios=frozenset(DEVICE_RADIOS),
        errors={'peak': 'Peak not saved.', 'quiet': 'Quiet not saved.'},
    )
    assert text == '<p class="error" role="alert">Peak not saved.</p>'
