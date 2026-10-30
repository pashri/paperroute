"""Tests for what the panel's settings screens show."""

from dataclasses import replace
from datetime import timedelta

import pytest

from paperroute.core.device_model import (
    Confirm,
    ConfirmAction,
    Menu,
    Origin,
    WifiStatus,
)
from paperroute.core.map_view import MapView, Marker
from paperroute.core.panel_screens import device_rows, panel_view, phone_view
from paperroute.core.panel_views import (
    CONFIRM_COPY,
    ConfirmView,
    InfoRow,
    PhoneView,
    confirm_view,
    sleep_map,
)
from paperroute.core.saved import Quota
from paperroute.core.state_model import State
from paperroute.core.wifi import wifi_escaped, wifi_qr
from tests.core.factories import START
from tests.core.test_rebuild import pinned
from tests.fakes import device_facts


def test_confirm_copy() -> None:
    """Every confirm says exactly what was agreed."""
    assert CONFIRM_COPY == {
        ConfirmAction.RESTART: (
            'Restart paperroute?',
            'paperroute starts again. Your pins and settings stay.',
            'restart',
        ),
        ConfirmAction.SHUT_DOWN: (
            'Shut down paperroute?',
            "paperroute switches off. The screen says when it's safe"
            ' to unplug.',
            'shut down',
        ),
        ConfirmAction.RESET_SETTINGS: (
            'Reset settings?',
            'Clears pins, settings, Wi-Fi and the key.'
            ' The timetable and map stay.',
            'reset',
        ),
        ConfirmAction.ERASE: (
            'Erase everything?',
            'Clears pins, settings, Wi-Fi, the key and the timetable.'
            ' The map stays.',
            'erase',
        ),
        ConfirmAction.JOIN_WIFI: (
            'Start Wi-Fi setup?',
            'paperroute leaves its Wi-Fi while you set up the new one.',
            'start',
        ),
    }


def test_confirm_view_lines() -> None:
    """A phone's ask says so; every confirm says what happens."""
    phone = Confirm(
        action=ConfirmAction.ERASE, origin=Origin.PHONE, asked_at=START,
    )
    assert confirm_view(confirm=phone) == ConfirmView(
        title='Settings',
        question='Erase everything?',
        lines=(
            'Asked for from a phone on your Wi-Fi.',
            'Clears pins, settings, Wi-Fi, the key and the timetable.'
            ' The map stays.',
        ),
        cancel='Top: press to cancel',
        confirm='Bottom: press to erase',
    )
    panel = replace(phone, action=ConfirmAction.RESTART, origin=Origin.PANEL)
    assert confirm_view(confirm=panel).lines == (
        'paperroute starts again. Your pins and settings stay.',
    )


def mocked() -> State:
    """
    Make a state whose Device screen matches the mock-up.

    Returns
    -------
    State
        The state.
    """
    state = pinned()
    facts = replace(
        device_facts(),
        wifi=WifiStatus(
            managed=True, ssid='Home', dbm=-55, ip='192.168.1.40',
        ),
        uptime_s=2.5 * 3600,
        version='a0e68ae',
        tiles_built='2026-10-19T03:00:00+00:00',
    )
    return replace(
        state,
        panel=replace(state.panel, facts=facts),
        saving=replace(state.saving, timetable='2026-10-21T10:45:00+00:00'),
        rebuild=replace(state.rebuild, schema='6'),
        quota=Quota(day=START.date(), calls=8412),
        last_fetch=START - timedelta(seconds=12),
    )


def test_device_rows_match_mock() -> None:
    """The Device screen reads as the mock-up."""
    assert device_rows(state=mocked()) == (
        InfoRow(label='Wi-Fi', value='Home \u00b7 strong \u00b7 192.168.1.40'),
        InfoRow(label='Clock', value='synced'),
        InfoRow(
            label='Timetable',
            value='21 Oct \u00b7 built 21:45 \u00b7 version 6',
        ),
        InfoRow(label='Map tiles', value='built 19 Oct'),
        InfoRow(
            label='Live data',
            value='12 s old \u00b7 8,412 calls today',
        ),
        InfoRow(label='Software', value='a0e68ae \u00b7 up 2 h'),
        InfoRow(label='Last error', value='none'),
    )


@pytest.mark.parametrize(
    ('dbm', 'strength'),
    [(-60, 'strong'), (-61, 'good'), (-70, 'good'), (-71, 'weak')],
)
def test_wifi_strength(dbm: int, strength: str) -> None:
    """Signal levels read strong, good or weak."""
    state = mocked()
    assert state.panel.facts is not None
    wifi = replace(state.panel.facts.wifi, dbm=dbm)
    facts = replace(state.panel.facts, wifi=wifi)
    state = replace(state, panel=replace(state.panel, facts=facts))
    assert device_rows(state=state)[0].value == (
        f'Home \u00b7 {strength} \u00b7 192.168.1.40'
    )


def test_device_rows_unknown() -> None:
    """With nothing known the rows read a dash."""
    state = pinned()
    values = [row.value for row in device_rows(state=state)]
    assert values == ['-', '-', '-', '-', '0 calls today', '-', '-']


def test_phone_view_without_ip() -> None:
    """With no IP the code opens paperroute.local."""
    assert phone_view(state=pinned()) == PhoneView(
        clock='8:00',
        url='http://paperroute.local/',
        addresses=('paperroute.local',),
        network=None,
    )


def test_panel_view_only_with_menu_or_confirm() -> None:
    """Nothing panel-made shows with the menu closed."""
    state = pinned()
    assert panel_view(state=state) is None
    menu = replace(state, panel=replace(state.panel, menu=Menu()))
    assert panel_view(state=menu) is not None


@pytest.mark.parametrize(
    ('text', 'escaped'),
    [
        ('plain', 'plain'),
        ('a;b', 'a\\;b'),
        ('a,b', 'a\\,b'),
        ('a:b', 'a\\:b'),
        ('a"b', 'a\\"b'),
        ('a\\b', 'a\\\\b'),
    ],
)
def test_wifi_escaped(text: str, escaped: str) -> None:
    """Special characters in a WIFI: field get a backslash."""
    assert wifi_escaped(text=text) == escaped


def test_wifi_qr() -> None:
    """The setup code joins the hotspot."""
    assert wifi_qr(password='7Q4K9W2M') == (
        'WIFI:T:WPA;S:paperroute-setup;P:7Q4K9W2M;;'
    )
    assert wifi_qr(password='a;b') == (
        'WIFI:T:WPA;S:paperroute-setup;P:a\\;b;;'
    )


def test_sleep_map_never_changes_with_time() -> None:
    """The sleep map has no clock, sidebar, markers or banner."""
    base = MapView(
        origin='A',
        destination=None,
        clock='8:00',
        base=None,  # type: ignore[arg-type]
        zoom=1,
        markers=(),
        sidebar=(),
        banner='x',
        empty=None,
    )
    later = replace(base, clock='8:01')
    assert sleep_map(view=base) == sleep_map(view=later)
    assert sleep_map(view=base).clock == ''
    assert sleep_map(view=base).banner is None


def test_sleep_map_strips_every_pane() -> None:
    """A two-pane map has no markers in any pane and never changes."""
    marker = Marker(
        route='T1', x=1, y=2, bearing=None, live=True, pinned=True,
    )

    def tick(*, clock: str) -> MapView:
        pane = MapView(
            origin='B',
            destination=None,
            clock=clock,
            base=None,  # type: ignore[arg-type]
            zoom=1,
            markers=(marker,),
            sidebar=(),
            banner='y',
            empty=None,
        )
        return replace(
            pane, origin='A', markers=(marker,), panes=(pane,),
        )

    asleep = sleep_map(view=tick(clock='8:00'))
    assert asleep.markers == ()
    assert all(pane.markers == () for pane in asleep.panes)
    assert all(pane.clock == '' for pane in asleep.panes)
    assert asleep == sleep_map(view=tick(clock='8:01'))
