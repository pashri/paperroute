"""Serve the device's own pages: Wi-Fi setup from a phone."""

import html
from collections.abc import Callable, Mapping
from http import HTTPStatus
from pathlib import Path
from typing import Final

from aiohttp import web

from paperroute.adapters.web_display import asset, form_fields
from paperroute.adapters.wifi import valid_password, valid_ssid
from paperroute.core.device_model import (
    DeviceStatus,
    JoinRequest,
    WifiNetwork,
    WifiPhase,
)
from paperroute.core.messages import ApiKeyChanged, Event, JoinAsked
from paperroute.ports import KeyStore

HIDDEN: Final[str] = '__hidden__'  # the radio value for a hidden network
STRONG: Final[int] = 67  # signal percent at or above which it is strong
GOOD: Final[int] = 34  # signal percent at or above which it is good
NOTICE_MARK: Final[str] = '{{notice}}'
FORM_MARK: Final[str] = '{{form}}'
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
BAD_KEY: Final[str] = "That doesn't look like a TfNSW API key."
KEY_FIELD: Final[str] = """
      <label for="api-key">TfNSW API key</label>
      <input id="api-key" name="api_key" type="text" autocomplete="off"
             autocapitalize="off" spellcheck="false">"""
FORM: Final[str] = """<form method="post" action="/setup">
      <fieldset>
        <legend>Your Wi-Fi</legend>
{networks}
        <label>
          <input type="radio" name="network" value="__hidden__">
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
        <button id="show-password" type="button" aria-controls="password"
                aria-pressed="false" hidden>Show password</button>
      </div>{key}
      <button type="submit">Join</button>
    </form>"""
RADIO: Final[str] = """        <label>
          <input type="radio" name="network" value="{ssid}"{disabled}>
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


def setup_form(*, status: DeviceStatus) -> str:
    """
    Draw the setup form.

    Parameters
    ----------
    status : DeviceStatus
        The device status, with setup in HOTSPOT.

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
        key=KEY_FIELD if status.key_set is False else '',
    )


def join_request(*, fields: Mapping[str, str]) -> JoinRequest | str:
    """
    Read the network asked for from the posted form.

    Parameters
    ----------
    fields : Mapping[str, str]
        `network`, `hidden_ssid` and `password`.

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
    if not valid_password(password=password):
        return BAD_PASSWORD
    return JoinRequest(ssid=ssid, password=password, hidden=hidden)


def notice(*, text: str, alert: bool = False) -> str:
    """
    Draw a line of news for the top of the page.

    Parameters
    ----------
    text : str
        The words; escaped here.
    alert : bool
        Whether it reports a refusal, so screen readers say it at once.

    Returns
    -------
    str
        A paragraph.
    """
    role = ' role="alert"' if alert else ''
    return f'<p class="notice"{role}>{html.escape(text)}</p>'


class DevicePages:
    """
    The pages a phone uses to set the device up.

    Nothing posted here is logged or drawn back into a page, and
    passwords and the key travel only in POST bodies.

    Parameters
    ----------
    status : Callable[[], DeviceStatus | None]
        The latest device status.
    feeds : Callable[[], Mapping[str, float]]
        When each feed last worked.
    key : KeyStore
        Where the TfNSW API key is kept.
    post : Callable[[Event], None]
        Sends an event to the app; called at request time.
    error_log : Path | None
        The error log, if any.
    state_file : Path | None
        The saved state, if any.
    """

    def __init__(
        self,
        *,
        status: Callable[[], DeviceStatus | None],
        feeds: Callable[[], Mapping[str, float]],
        key: KeyStore,
        post: Callable[[Event], None],
        error_log: Path | None,
        state_file: Path | None,
    ) -> None:
        self._status = status
        self.feeds = feeds
        self._key = key
        self._post = post
        self.error_log = error_log
        self.state_file = state_file

    def routes(self) -> list[web.RouteDef]:
        """
        List the device pages' routes.

        Returns
        -------
        list[web.RouteDef]
            The setup page, its script and its form post.
        """
        return [
            web.get('/setup', self._setup_page),
            web.get('/setup.js', self._setup_js),
            web.post('/setup', self._setup),
        ]

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
            form = setup_form(status=device)
        page = asset(name='setup.html').replace(
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
            page = asset(name='setup.html').replace(
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
            text=asset(name='setup.js'),
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
            bad network, password or key, otherwise 303 to the joining
            page.
        """
        if self._hotspot() is None:
            return self._page(status=HTTPStatus.CONFLICT)
        fields = await form_fields(request=request)
        asked = join_request(fields=fields)
        if isinstance(asked, str):
            return self._refuse(reason=asked)
        if not self._store_key(key=fields.get('api_key', '').strip()):
            return self._refuse(reason=BAD_KEY)
        self._post(JoinAsked(request=asked))
        raise web.HTTPSeeOther(location='/setup?joining=1')

    def _refuse(self, *, reason: str) -> web.Response:
        """
        Draw the page again with why the post was refused.

        Parameters
        ----------
        reason : str
            The refusal.

        Returns
        -------
        web.Response
            A 400 page with an empty form.
        """
        return self._page(
            news=notice(text=reason, alert=True),
            status=HTTPStatus.BAD_REQUEST,
        )

    def _store_key(self, *, key: str) -> bool:
        """
        Store a posted key and tell the app.

        Parameters
        ----------
        key : str
            The key; empty when none was given.

        Returns
        -------
        bool
            False when the store refused the key.
        """
        if not key:
            return True
        try:
            self._key.write(key=key)
        except ValueError:
            return False
        self._post(ApiKeyChanged(present=True))
        return True
