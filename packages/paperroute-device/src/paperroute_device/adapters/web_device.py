"""Serve the device's own pages: Wi-Fi setup and the Device page."""

import html
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import tzinfo
from http import HTTPStatus
from pathlib import Path
from typing import Final

from aiohttp import web

from paperroute_device.adapters.device_page import (
    RESETS,
    data_rows,
    device_notice,
    feed_rows,
    key_text,
    knob_rows,
    network_rows,
    notice,
    panel_rows,
    power_status,
    system_rows,
)
from paperroute_device.adapters.web_common import form_fields
from paperroute_device.adapters.web_frame import (
    KIT_NAV,
    NavItem,
    page_frame,
    shell_text,
)
from paperroute_device.adapters.wifi import valid_password, valid_ssid
from paperroute_device.core.device_events import (
    ApiKeyChanged,
    ConfirmAsked,
    DeviceRequest,
    JoinAsked,
    PowerRequested,
)
from paperroute_device.core.device_model import (
    POWER_ACTIONS,
    ConfirmAction,
    Credential,
    DeviceStatus,
    JoinRequest,
    PowerAction,
    WifiNetwork,
    WifiPhase,
)
from paperroute_device.ports import KeyStore
from paperroute_hardware.ports import PanelTimes

logger = logging.getLogger(__name__)

HIDDEN: Final[str] = '__hidden__'  # the radio value for a hidden network
STRONG: Final[int] = 67  # signal percent at or above which it is strong
GOOD: Final[int] = 34  # signal percent at or above which it is good
NOTICE_MARK: Final[str] = '{{notice}}'
KEY_LABEL_MARK: Final[str] = '{{key_label}}'
FORM_MARK: Final[str] = '{{form}}'
DOWNLOAD_TYPES: Final[dict[str, str]] = {
    'errors.jsonl': 'application/x-ndjson',
    'state.json': 'application/json',
}
NOT_RUNNING: Final[str] = (
    "Wi-Fi setup isn't running. On the device, hold both knobs, then "
    'choose Wi-Fi.'
)
JOINING: Final[str] = (
    'Joining... Your phone will drop off paperroute-setup. If the '
    'password is wrong, paperroute-setup comes back: join it again and '
    'try once more.'
)
BAD_SSID: Final[str] = (
    "Choose a network, or type the hidden network's name (up to 32 "
    'bytes).'
)
BAD_PASSWORD: Final[str] = 'Wi-Fi passwords are 8 to 63 characters.'
UNSUPPORTED: Final[str] = 'That network is not supported.'
KEY_NOT_SAVED: Final[str] = "Couldn't save the key on the device. Try again."
KEY_FIELD: Final[str] = """
      <label for="api-key">{{key_label}}</label>
      <input id="api-key" name="api_key" type="text" autocomplete="off"
             autocapitalize="off" spellcheck="false">"""
FORM: Final[str] = """<form method="post" action="/setup">
      <fieldset>
        <legend>Your Wi-Fi</legend>
{networks}
        <label>
          <input type="radio" name="network" value="__hidden__" required>
          Hidden network
        </label>
        <label for="hidden-ssid">Hidden network name</label>
        <input id="hidden-ssid" name="hidden_ssid" type="text"
               autocomplete="off" autocapitalize="off" spellcheck="false">
      </fieldset>
      <label for="password">Password</label>
      <div class="password">
        <input id="password" name="password" type="password"
               autocomplete="current-password">
        <button id="show-password" type="button" class="secondary"
                aria-controls="password"
                aria-pressed="false" hidden>Show password</button>
      </div>{key}
      <button type="submit">Join</button>
    </form>"""
CONFIRM: Final[dict[PowerAction, tuple[str, str]]] = {
    PowerAction.OFF: (
        "Shut down now? The screen will show OFF when it's safe to unplug.",
        'Shut down',
    ),
    PowerAction.RESTART: ('Restart now?', 'Restart'),
}
RADIO: Final[str] = """        <label>
          <input type="radio" name="network" value="{ssid}" required{disabled}>
          {label}
        </label>"""


def strength(*, signal: int) -> str:
    """
    Name a signal level.

    Parameters
    ----------
    signal : int
        Signal strength, 0 to 100.

    Returns
    -------
    str
        `strong`, `good` or `weak`.
    """
    if signal >= STRONG:
        return 'strong'
    return 'good' if signal >= GOOD else 'weak'


def network_label(*, network: WifiNetwork) -> str:
    """
    Describe a scanned network for its radio button.

    Parameters
    ----------
    network : WifiNetwork
        The network.

    Returns
    -------
    str
        The name, its strength, and whether it is open or unsupported;
        not escaped.
    """
    label = f'{network.ssid} ({strength(signal=network.signal)})'
    if not network.secured:
        label += ' (open)'
    if network.enterprise:
        label += ' (not supported)'
    return label


def network_radio(*, network: WifiNetwork) -> str:
    """
    Draw one scanned network as a radio button.

    Parameters
    ----------
    network : WifiNetwork
        The network.

    Returns
    -------
    str
        Escaped HTML; enterprise networks are disabled.
    """
    return RADIO.format(
        ssid=html.escape(network.ssid),
        disabled=' disabled' if network.enterprise else '',
        label=html.escape(network_label(network=network)),
    )


def setup_form(*, status: DeviceStatus, key_label: str) -> str:
    """
    Draw the setup form.

    Parameters
    ----------
    status : DeviceStatus
        The device status, with setup in HOTSPOT.
    key_label : str
        The key field's label; escaped here.

    Returns
    -------
    str
        The form; it asks for a key only when none is set.
    """
    networks = () if status.setup is None else status.setup.networks
    return FORM.format(
        networks='\n'.join(
            network_radio(network=network) for network in networks
        ),
        key=(
            KEY_FIELD.replace(KEY_LABEL_MARK, html.escape(key_label))
            if status.key_set is False
            else ''
        ),
    )


def join_request(
    *, fields: Mapping[str, str], networks: tuple[WifiNetwork, ...] = ()
) -> JoinRequest | str:
    """
    Read the network asked for from the posted form.

    Parameters
    ----------
    fields : Mapping[str, str]
        `network`, `hidden_ssid` and `password`.
    networks : tuple[WifiNetwork, ...]
        The scanned networks; enterprise ones are refused.

    Returns
    -------
    JoinRequest | str
        The request, or why it was refused.
    """
    hidden = fields.get('network') == HIDDEN
    ssid = fields.get('hidden_ssid' if hidden else 'network', '')
    password = fields.get('password', '')
    if not valid_ssid(ssid=ssid):
        return BAD_SSID
    if not hidden and any(
        network.enterprise and network.ssid == ssid for network in networks
    ):
        return UNSUPPORTED
    if not valid_password(password=password):
        return BAD_PASSWORD
    return JoinRequest(ssid=ssid, password=password, hidden=hidden)


class DevicePages:
    """
    The pages a phone uses to set the device up and look after it.

    Nothing posted here is logged or drawn back into a page, and
    passwords and the key travel only in POST bodies.

    Parameters
    ----------
    status : Callable[[], DeviceStatus | None]
        The latest device status.
    feeds : Callable[[], Mapping[str, float]]
        When each feed last worked.
    panel : Callable[[], PanelTimes | None]
        The panel's refresh times, or None when there is no panel.
    key : KeyStore
        Where the API key is kept.
    credential : Credential
        How the pages name the key and refuse a bad one.
    feed_label : Callable[[str], str]
        Names a feed for people.
    daily_limit : int
        The API calls allowed each day.
    post : Callable[[DeviceRequest], None]
        Sends a request to the app; called at request time.
    zone : tzinfo
        Time zone whose wall clock the Device page shows.
    error_log : Path | None
        The error log, if any.
    state_file : Path | None
        The saved state, if any.
    clock : Callable[[], float]
        The wall-clock time, as `time.time` gives it.
    knobs : Callable[[], bool | None]
        Whether the knobs' lines opened, or None when not asked for.
    nav : Sequence[NavItem]
        The links in each page's nav, in order.
    """

    def __init__(  # pylint: disable=too-many-arguments
        self,
        *,
        status: Callable[[], DeviceStatus | None],
        feeds: Callable[[], Mapping[str, float]],
        panel: Callable[[], PanelTimes | None],
        key: KeyStore,
        credential: Credential,
        feed_label: Callable[[str], str],
        daily_limit: int,
        post: Callable[[DeviceRequest], None],
        zone: tzinfo,
        error_log: Path | None,
        state_file: Path | None,
        clock: Callable[[], float] = time.time,
        knobs: Callable[[], bool | None] = lambda: None,
        nav: Sequence[NavItem] = KIT_NAV,
    ) -> None:
        self._status = status
        self._feeds = feeds
        self._panel = panel
        self._key = key
        self._credential = credential
        self._feed_label = feed_label
        self._daily_limit = daily_limit
        self._post = post
        self._zone = zone
        self._error_log = error_log
        self._state_file = state_file
        self._clock = clock
        self._knobs = knobs
        self._nav = tuple(nav)

    def _routes(self) -> list[web.RouteDef]:
        """
        List the device pages' routes.

        Returns
        -------
        list[web.RouteDef]
            The power confirm page and post, the setup page, the Device
            page, their scripts, form posts and downloads.
        """
        return [
            web.get('/device/power', self._power_page),
            web.post('/device/power', self._power_page),
            web.post('/power', self._power),
            web.get('/setup', self._setup_page),
            web.get('/setup.js', self._setup_js),
            web.post('/setup', self._setup),
            web.get('/device', self._device_page),
            web.get('/device.js', self._device_js),
            web.post('/device/wifi', self._join_other),
            web.post('/device/reset', self._reset),
            web.post('/device/key', self._replace_key),
            web.get('/device/errors.jsonl', self._download),
            web.get('/device/state.json', self._download),
        ]

    def _frame(self, *, name: str, current: str | None) -> str:
        """
        Read a page with this site's nav drawn in.

        Parameters
        ----------
        name : str
            The page's file name.
        current : str | None
            The href the nav marks as the open page, or None.

        Returns
        -------
        str
            The page, its other marks still unfilled.
        """
        return page_frame(name=name, nav=self._nav, current=current)

    def _hotspot(self) -> DeviceStatus | None:
        """
        Get the status while the setup hotspot is up.

        Returns
        -------
        DeviceStatus | None
            The status, or None when the hotspot is not up.
        """
        status = self._status()
        if status is None or status.setup is None:
            return None
        return status if status.setup.phase is WifiPhase.HOTSPOT else None

    def _page(
        self, *, news: str = '', status: HTTPStatus = HTTPStatus.OK
    ) -> web.Response:
        """
        Draw the setup page, with the form while the hotspot is up.

        Parameters
        ----------
        news : str
            HTML for the top of the page.
        status : HTTPStatus
            The response status.

        Returns
        -------
        web.Response
            The page.
        """
        device = self._hotspot()
        if device is None:
            news, form = notice(text=NOT_RUNNING), ''
        else:
            form = setup_form(
                status=device, key_label=self._credential.label,
            )
        page = self._frame(name='setup.html', current=None).replace(
            NOTICE_MARK, news,
        ).replace(FORM_MARK, form)
        return web.Response(
            text=page, content_type='text/html', status=status,
        )

    async def _setup_page(self, request: web.Request) -> web.Response:
        """
        Serve the setup page.

        Parameters
        ----------
        request : web.Request
            The incoming request; `joining=1` after a post.

        Returns
        -------
        web.Response
            The page, or the joining news alone.
        """
        if request.query.get('joining') == '1':
            page = self._frame(name='setup.html', current=None).replace(
                NOTICE_MARK, notice(text=JOINING),
            ).replace(FORM_MARK, '')
            return web.Response(text=page, content_type='text/html')
        device = self._hotspot()
        error = None if device is None or device.setup is None else (
            device.setup.error
        )
        return self._page(news=notice(text=error) if error else '')

    async def _setup_js(self, request: web.Request) -> web.Response:
        """
        Serve the setup page's script.

        Parameters
        ----------
        request : web.Request
            The incoming request.

        Returns
        -------
        web.Response
            The response.
        """
        del request
        return web.Response(
            text=shell_text(name='setup.js'),
            content_type='text/javascript',
        )

    async def _setup(self, request: web.Request) -> web.Response:
        """
        Store a posted key, then ask the app to join the posted network.

        Parameters
        ----------
        request : web.Request
            The incoming request.

        Returns
        -------
        web.Response
            409 when the hotspot is not up, 400 with the reason for a
            bad network, password or key, 500 when the key can't be
            saved, otherwise 303 to the joining page. A key is taken
            only on a device without one.
        """
        device = self._hotspot()
        if device is None or device.setup is None:
            return self._page(status=HTTPStatus.CONFLICT)
        fields = await form_fields(request=request)
        asked = join_request(fields=fields, networks=device.setup.networks)
        if isinstance(asked, str):
            return self._refuse(reason=asked)
        refusal = self._store_key(
            key=(
                fields.get('api_key', '').strip()
                if device.key_set is False
                else ''
            ),
        )
        if refusal is not None:
            return refusal
        self._post(JoinAsked(request=asked))
        raise web.HTTPSeeOther(location='/setup?joining=1')

    def _refuse(
        self,
        *,
        reason: str,
        status: HTTPStatus = HTTPStatus.BAD_REQUEST,
    ) -> web.Response:
        """
        Draw the page again with why the post was refused.

        Parameters
        ----------
        reason : str
            The refusal.
        status : HTTPStatus
            The response status.

        Returns
        -------
        web.Response
            The page with an empty form.
        """
        return self._page(news=notice(text=reason, alert=True), status=status)

    def _store_key(self, *, key: str) -> web.Response | None:
        """
        Store a posted key and tell the app.

        Parameters
        ----------
        key : str
            The key; empty when none was given.

        Returns
        -------
        web.Response | None
            A refusal page when the key is invalid or can't be saved,
            else None.
        """
        if not key:
            return None
        try:
            self._key.write(key=key)
        except ValueError:
            return self._refuse(reason=self._credential.refusal)
        except OSError:
            logger.exception('could not save the API key file')
            return self._refuse(
                reason=KEY_NOT_SAVED,
                status=HTTPStatus.INTERNAL_SERVER_ERROR,
            )
        self._post(ApiKeyChanged(present=True))
        return None

    def _device(
        self, *, news: str = '', power: str = '',
        status: HTTPStatus = HTTPStatus.OK,
    ) -> web.Response:
        """
        Draw the Device page from the latest status and live facts.

        Parameters
        ----------
        news : str
            HTML for the top of the page.
        power : str
            The power action under way, or empty.
        status : HTTPStatus
            The response status.

        Returns
        -------
        web.Response
            The page; the key itself is never drawn.
        """
        device, now = self._status(), self._clock()
        feeds = self._feeds()
        facts = None if device is None else device.facts
        marks = {
            KEY_LABEL_MARK: html.escape(self._credential.label),
            NOTICE_MARK: news,
            '{{network}}': network_rows(facts=facts),
            '{{data}}': data_rows(
                status=device,
                daily_limit=self._daily_limit,
                zone=self._zone,
            ),
            '{{feeds}}': feed_rows(
                feeds=feeds,
                now=now,
                label=self._feed_label,
                zone=self._zone,
            ),
            '{{system}}': system_rows(facts=facts),
            '{{panel}}': (
                panel_rows(panel=self._panel())
                + knob_rows(knobs=self._knobs())
            ),
            '{{key_status}}': html.escape(key_text(
                present=self._key.present(),
                last_used=max(feeds.values(), default=None),
                now=now,
            )),
            '{{device_status}}': html.escape(power),
        }
        page = self._frame(name='device.html', current='/device')
        for mark, value in marks.items():
            page = page.replace(mark, value)
        return web.Response(
            text=page, content_type='text/html', status=status,
        )

    async def _device_page(self, request: web.Request) -> web.Response:
        """
        Serve the Device page.

        Parameters
        ----------
        request : web.Request
            The incoming request; `asked`, `key` or `power` after a post.

        Returns
        -------
        web.Response
            The page.
        """
        return self._device(
            news=device_notice(query=request.query),
            power=power_status(query=request.query),
        )

    async def _device_js(self, request: web.Request) -> web.Response:
        """
        Serve the Device page's script.

        Parameters
        ----------
        request : web.Request
            The incoming request.

        Returns
        -------
        web.Response
            The response.
        """
        del request
        return web.Response(
            text=shell_text(name='device.js'),
            content_type='text/javascript',
        )

    async def _join_other(self, request: web.Request) -> web.Response:
        """
        Ask the panel to start Wi-Fi setup.

        Parameters
        ----------
        request : web.Request
            The incoming request.

        Returns
        -------
        web.Response
            Never returns; 303 to the page saying to confirm.
        """
        del request
        self._post(ConfirmAsked(action=ConfirmAction.JOIN_WIFI))
        raise web.HTTPSeeOther(
            location=f'/device?asked={ConfirmAction.JOIN_WIFI.value}',
        )

    async def _reset(self, request: web.Request) -> web.Response:
        """
        Ask the panel to reset settings or erase everything.

        Parameters
        ----------
        request : web.Request
            The incoming request; `kind` is `settings` or `erase`.

        Returns
        -------
        web.Response
            400 for an unknown kind, else 303 to the page saying to
            confirm.
        """
        fields = await form_fields(request=request)
        action = RESETS.get(fields.get('kind', ''))
        if action is None:
            return web.Response(status=HTTPStatus.BAD_REQUEST)
        self._post(ConfirmAsked(action=action))
        raise web.HTTPSeeOther(location=f'/device?asked={action.value}')

    async def _replace_key(self, request: web.Request) -> web.Response:
        """
        Store a posted key in place of any old one.

        Parameters
        ----------
        request : web.Request
            The incoming request; `api_key` in the form.

        Returns
        -------
        web.Response
            400 for a bad key, 500 when it can't be saved, otherwise
            303 to the page saying it was saved.
        """
        fields = await form_fields(request=request)
        refusal = self._key_refusal(key=fields.get('api_key', '').strip())
        if refusal is not None:
            return refusal
        self._post(ApiKeyChanged(present=True))
        raise web.HTTPSeeOther(location='/device?key=saved')

    def _key_refusal(self, *, key: str) -> web.Response | None:
        """
        Save a key in place of any old one.

        Parameters
        ----------
        key : str
            The key, trimmed.

        Returns
        -------
        web.Response | None
            The device page with why it was refused: 400 for a bad key,
            500 when it can't be saved; None once saved.
        """
        try:
            if not key:
                raise ValueError('no key given')
            self._key.write(key=key)
        except ValueError:
            return self._device(
                news=notice(text=self._credential.not_saved, alert=True),
                status=HTTPStatus.BAD_REQUEST,
            )
        except OSError:
            logger.exception('could not save the API key file')
            return self._device(
                news=notice(text=KEY_NOT_SAVED, alert=True),
                status=HTTPStatus.INTERNAL_SERVER_ERROR,
            )
        return None

    async def _download(self, request: web.Request) -> web.StreamResponse:
        """
        Serve the error log or the saved state as a download.

        Parameters
        ----------
        request : web.Request
            The incoming request; its last path part names the file.

        Returns
        -------
        web.StreamResponse
            The file as an attachment, never cached; 404 when there is
            none.
        """
        name = request.path.rsplit('/', maxsplit=1)[-1]
        path = {
            'errors.jsonl': self._error_log,
            'state.json': self._state_file,
        }[name]
        if path is None or not path.is_file():
            return web.Response(status=HTTPStatus.NOT_FOUND)
        return web.FileResponse(path, headers={
            'Content-Type': DOWNLOAD_TYPES[name],
            'Content-Disposition': f'attachment; filename="{name}"',
            'Cache-Control': 'no-store',
        })

    async def _power_page(self, request: web.Request) -> web.Response:
        """
        Serve the page that confirms a shutdown or restart.

        Parameters
        ----------
        request : web.Request
            The incoming request; `action` in the query.

        Returns
        -------
        web.Response
            The page, or 400 for an unknown action.
        """
        action = POWER_ACTIONS.get(request.query.get('action', ''))
        if action is None:
            return web.Response(status=HTTPStatus.BAD_REQUEST)
        question, confirm = CONFIRM[action]
        tone = ' class="danger"' if action is PowerAction.OFF else ''
        page = self._frame(name='power.html', current='/device').replace(
            '{{question}}', question,
        ).replace('{{action}}', action.value).replace(
            '{{confirm}}', confirm,
        ).replace('{{tone}}', tone)
        return web.Response(text=page, content_type='text/html')

    async def _power(self, request: web.Request) -> web.Response:
        """
        Post a shutdown or restart from the browser.

        Parameters
        ----------
        request : web.Request
            The incoming request; `action` is `poweroff` or `reboot`.

        Returns
        -------
        web.Response
            400 for an unknown action; otherwise 204 to the script's
            JSON, or 303 to the Device page for a plain form.
        """
        fields = await form_fields(request=request)
        action = POWER_ACTIONS.get(fields.get('action', ''))
        if action is None:
            return web.Response(status=HTTPStatus.BAD_REQUEST)
        self._post(PowerRequested(action=action))
        if request.content_type == 'application/json':
            return web.Response(status=HTTPStatus.NO_CONTENT)
        raise web.HTTPSeeOther(location=f'/device?power={action.value}')
