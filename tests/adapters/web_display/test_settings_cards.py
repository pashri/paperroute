"""Tests for web display: settings cards."""

import re
from html.parser import HTMLParser
from http import HTTPStatus
from typing import Final

from paperroute_sydney.city import SYDNEY
from paperroute_testkit.core.kit_steps import settings_posted
from paperroute_transit.core.saved import Lookahead
from tests.adapters.transit_web import transit_display
from tests.adapters.web_display.helpers import Client, _client, page_text


async def test_every_settings_card_has_its_own_save(client: Client) -> None:
    """Each card's form has a visible Save and a note for the script."""
    text = await (await client.get('/settings')).text()
    forms = [
        chunk.split('</form>')[0]
        for chunk in text.split('<form')[1:]
    ]
    assert len(forms) == 4
    for form in forms:
        assert 'action="/settings"' in form
        buttons = re.findall(r'<button[^>]*type="submit"[^>]*>', form)
        assert len(buttons) == 1
        assert 'hidden' not in buttons[0]
        assert form.count('class="hint saved"') == 1
    assert 'timetable-save' not in text
    assert 'settings-status' not in text


async def test_unknown_value_is_400(
    posted: list[object], client: Client
) -> None:
    """A rate the page never sends is refused."""
    response = await client.post('/settings', data={'peak_rate': '1s'})
    assert response.status == HTTPStatus.BAD_REQUEST
    assert not posted


async def test_stale_timetable_quiet_mode_is_400(
    posted: list[object], client: Client
) -> None:
    """A stale page's `timetable` quiet mode is refused."""
    response = await client.post(
        '/settings', data={'quiet_mode': 'timetable'},
    )
    assert response.status == HTTPStatus.BAD_REQUEST
    assert not posted


DEVICE_CONTROLS: Final[tuple[str, ...]] = (
    'rotate', 'swap_knobs', 'larger_text', 'inverted_lists', 'clean_after',
    'quiet_mode', 'quiet_start', 'quiet_end',
)


async def test_device_section_comes_first_and_quiet_hours_is_gone(
    client: Client,
) -> None:
    """Device comes first, holding every device control and quiet mode."""
    text = await (await client.get('/settings')).text()
    assert re.findall(r'<h2 id="([a-z]+)">', text) == [
        'device', 'display', 'alerts', 'timetable',
    ]
    assert re.findall(r'<form id="([a-z]+)-form"', text) == [
        'device', 'display', 'alerts', 'timetable',
    ]
    assert 'Quiet hours</h2>' not in text
    device, rest = text.split('<form id="device-form"')[1].split('</form>', 1)
    names = set(re.findall(r'name="([a-z_]+)"', device))
    assert names == set(DEVICE_CONTROLS)
    assert not names & set(re.findall(r'name="([a-z_]+)"', rest))
    assert '<legend>When the screen sleeps</legend>' in device
    assert re.findall(r'name="quiet_mode" value="([a-z_]+)"', device) == [
        'my_hours', 'app',
    ]
    assert 'Follow the timetable' in device


async def test_unknown_lookahead_is_400(
    posted: list[object], client: Client
) -> None:
    """A lookahead the page never sends is refused."""
    response = await client.post(
        '/settings', data={'alert_lookahead': '2w'},
    )
    assert response.status == HTTPStatus.BAD_REQUEST
    assert not posted


async def test_alerts_section_posts_event(
    posted: list[object], client: Client
) -> None:
    """A form post of the Alerts section posts one event and redirects."""
    response = await client.post(
        '/settings',
        data=[('alert_lookahead', 'all')],
        allow_redirects=False,
    )
    assert response.status == HTTPStatus.SEE_OTHER
    assert response.headers['Location'] == '/settings'
    assert posted == [settings_posted(alert_lookahead=Lookahead.ALL)]


async def test_settings_cross_site_403(
    posted: list[object], client: Client
) -> None:
    """A post from another site is refused."""
    response = await client.post(
        '/settings',
        data={'rotate': 'on'},
        headers={'Origin': 'http://evil.example'},
    )
    assert response.status == HTTPStatus.FORBIDDEN
    assert not posted


async def test_slot_two_save_overlay_clears() -> None:
    """Only Afternoon filled: the app reports it as the first slot."""
    status: dict[str, object] = {'settings': {'peak_1_start': '06:30'}}
    display = transit_display(status=lambda: status, city=SYDNEY)
    client = await _client(display=display)
    fields = {
        'peak_1_start': '', 'peak_1_end': '',
        'peak_2_start': '15:00', 'peak_2_end': '19:00',
    }
    try:
        await client.post('/settings', data=fields)
        shown = await (await client.get('/settings')).text()
        assert 'name="peak_1_start" value="15:00"' in shown
        status['settings'] = {
            'peak_1_start': '15:00', 'peak_1_end': '19:00',
            'peak_2_start': '', 'peak_2_end': '',
        }
        await client.get('/settings')
    finally:
        await client.close()
    assert display.settings is not None
    assert not display.settings._posted  # pylint: disable=protected-access


async def test_later_panel_change_shows_on_page() -> None:
    """A change at the panel after a save is not hidden by the overlay."""
    status: dict[str, object] = {'settings': {'rotate': 'off'}}
    display = transit_display(status=lambda: status, city=SYDNEY)
    client = await _client(display=display)
    try:
        await client.post('/settings', data={'rotate': 'on'})
        status['settings'] = {'rotate': 'off', 'larger_text': 'on'}
        text = await (await client.get('/settings')).text()
    finally:
        await client.close()
    assert re.search(r'name="rotate"\s+value="on">', text)
    assert re.search(r'name="larger_text"\s+value="on" checked>', text)


async def test_page_shows_posted_before_status() -> None:
    """The posted value wins until the status reports it."""
    status: dict[str, object] = {'settings': {'rotate': 'off'}}
    display = transit_display(status=lambda: status, city=SYDNEY)
    client = await _client(display=display)
    rotate = re.compile(r'name="rotate"\s+value="on" checked>')
    try:
        await client.post('/settings', data={'rotate': 'on'})
        shown = await (await client.get('/settings')).text()
        assert rotate.search(shown)
        assert display.settings is not None
        assert display.settings._posted == {'rotate': 'on'}  # pylint: disable=protected-access
        status['settings'] = {'rotate': 'on'}
        later = await (await client.get('/settings')).text()
    finally:
        await client.close()
    assert rotate.search(later)
    assert display.settings is not None
    assert not display.settings._posted  # pylint: disable=protected-access


class _Controls(HTMLParser):
    """Collect the controls outside forms and the unlabelled ones."""

    def __init__(self) -> None:
        super().__init__()
        self.in_form = False
        self.loose: list[str] = []
        self.forms: list[dict[str, str | None]] = []
        self.submits: list[int] = []
        self.label_depth = 0
        self.unlabelled: list[str] = []
        self.for_ids: set[str] = set()
        self.control_ids: list[tuple[str, str | None]] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        """Track forms, labels and controls."""
        found = dict(attrs)
        if tag == 'form':
            self.in_form = True
            self.forms.append(found)
            self.submits.append(0)
        elif tag == 'label':
            self.label_depth += 1
            if found.get('for'):
                self.for_ids.add(str(found['for']))
        elif tag in {'input', 'select', 'button'}:
            self._control(tag=tag, found=found)

    def _control(self, *, tag: str, found: dict[str, str | None]) -> None:
        """Record one control."""
        kind = found.get('type')
        if tag == 'input' and kind == 'hidden':
            return
        if not self.in_form:
            self.loose.append(tag)
        if tag == 'button' and kind == 'submit':
            self.submits[-1] += 1
        if tag != 'button' and not self.label_depth:
            self.control_ids.append((tag, found.get('id')))

    def handle_endtag(self, tag: str) -> None:
        """Close forms and labels."""
        if tag == 'form':
            self.in_form = False
        elif tag == 'label':
            self.label_depth -= 1


async def test_page_works_without_script() -> None:
    """Every control is in a post form with Save; each has a label."""
    display = transit_display(city=SYDNEY)
    text = await page_text(display=display, path='/settings')
    parser = _Controls()
    parser.feed(text)
    assert not parser.loose
    assert len(parser.forms) == 4
    assert all(form['method'] == 'post' for form in parser.forms)
    assert all(form['action'] == '/settings' for form in parser.forms)
    assert all(count >= 1 for count in parser.submits)
    for tag, ident in parser.control_ids:
        assert ident in parser.for_ids, tag
