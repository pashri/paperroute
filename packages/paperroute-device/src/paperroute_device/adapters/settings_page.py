"""Serve the Settings page: the device's section and the app's own."""

# A copy of the board app's settings handler, until it uses this one.
# pylint: disable=duplicate-code

from collections.abc import Callable, Mapping, Sequence
from dataclasses import fields as dataclass_fields
from dataclasses import replace
from html import escape
from http import HTTPStatus
from typing import Final, Protocol

from aiohttp import web

from paperroute_device.adapters.device_form import (
    DEVICE_RADIOS,
    DEVICE_SWITCHES,
    ERRORS,
    BadInput,
    UnknownValue,
    device_change_from,
    device_values,
    page_text,
)
from paperroute_device.adapters.web_common import form_fields
from paperroute_device.adapters.web_frame import (
    KIT_NAV,
    NavItem,
    page_frame,
    shell_text,
)
from paperroute_device.core.device_settings import DeviceSettings
from paperroute_device.core.kit_events import DeviceChange, SettingsPosted

QUIET_APP_MARK: Final[str] = '{{quiet_app}}'
APP_SECTIONS_MARK: Final[str] = '{{app_sections}}'
LABEL_MARK: Final[str] = '{label}'
QUIET_APP_RADIO: Final[str] = (
    '          <label class="switch">\n'
    '            <input type="radio" name="quiet_mode" value="app"'
    '{{quiet_mode:app}}>\n'
    '            {label}\n'
    '          </label>\n'
)


class SettingsPlug(Protocol):
    """The app's sections of the Settings page and how its fields read."""

    @property
    def switches(self) -> frozenset[str]:
        """
        Name the app's switches.

        Returns
        -------
        frozenset[str]
            Each switch field, drawn ` checked` when `on`.
        """

    @property
    def radios(self) -> frozenset[str]:
        """
        Name the app's radio groups.

        Returns
        -------
        frozenset[str]
            Each radio field, whose choice marks draw ` checked`.
        """

    @property
    def sheets(self) -> tuple[str, ...]:
        """
        Name the stylesheets the app's sections need.

        Returns
        -------
        tuple[str, ...]
            Stylesheets the app serves itself, linked on the Settings
            page straight after every page's own extra sheets.
        """

    @property
    def errors(self) -> Mapping[str, str]:
        """
        Give the message of each of the app's sections that can refuse.

        Returns
        -------
        Mapping[str, str]
            The message by section, shown at `{{error_<section>}}`.
        """

    def sections(self) -> str:
        """
        Write the app's sections.

        Returns
        -------
        str
            Whole lines of markup, each indented as it appears on the
            page, holding `{{field}}` marks; empty for none.
        """

    def values(self, *, status: Mapping[str, object]) -> dict[str, str]:
        """
        Read the app's form values from the status fields.

        Parameters
        ----------
        status : Mapping[str, object]
            The status page's fields.

        Returns
        -------
        dict[str, str]
            The app's form values; its defaults until it reports.
        """

    def change_from(self, *, fields: Mapping[str, str]) -> object | None:
        """
        Turn posted fields into the app's change.

        Parameters
        ----------
        fields : Mapping[str, str]
            The posted fields, the device's among them.

        Returns
        -------
        object | None
            The change handed to the app, or None when no field of the
            app's was posted.

        Raises
        ------
        UnknownValue
            When a field holds a value the page never sends.
        BadInput
            When a typed value is not usable; its section names one of
            `errors`.
        """

    def posted_values(
        self, *, fields: Mapping[str, str], change: object,
    ) -> dict[str, str]:
        """
        Write the app's posted fields the way the app will report them.

        Parameters
        ----------
        fields : Mapping[str, str]
            The posted fields.
        change : object
            The change `change_from` read from them.

        Returns
        -------
        dict[str, str]
            The form values of the app's posted fields.
        """


def device_posted_values(
    *, fields: Mapping[str, str], change: DeviceChange,
) -> dict[str, str]:
    """
    Write the posted device fields the way the kit will report them.

    Parameters
    ----------
    fields : Mapping[str, str]
        The posted fields.
    change : DeviceChange
        The device change read from them.

    Returns
    -------
    dict[str, str]
        The form values of the device fields that were posted.
    """
    given = {
        item.name: getattr(change, item.name)
        for item in dataclass_fields(change)
        if getattr(change, item.name) is not None
    }
    full = device_values(settings=replace(DeviceSettings(), **given))
    return {key: full[key] for key in fields if key in full}


def device_reported(*, status: Mapping[str, object]) -> dict[str, str]:
    """
    Read the device's form values from the status fields.

    Parameters
    ----------
    status : Mapping[str, object]
        The status page's fields; `settings` holds the reported form
        values.

    Returns
    -------
    dict[str, str]
        The device's form values, over its defaults.
    """
    values = device_values(settings=DeviceSettings())
    reported = status.get('settings')
    if isinstance(reported, Mapping):
        values.update({
            key: value
            for key, value in reported.items()
            if key in values and isinstance(value, str)
        })
    return values


def line_filled(*, page: str, mark: str, lines: str) -> str:
    """
    Put whole lines in place of the line holding a mark.

    Parameters
    ----------
    page : str
        The page, with the mark alone on one line.
    mark : str
        The mark.
    lines : str
        Whole lines; empty drops the mark's line, leaving no blank line.

    Returns
    -------
    str
        The page with the lines where the mark's line was.
    """
    if lines and not lines.endswith('\n'):
        lines = f'{lines}\n'
    head, found, tail = page.partition(mark)
    if not found:
        return page
    start = head.rfind('\n') + 1
    return f'{head[:start]}{lines}{tail.removeprefix('\n')}'


def quiet_app_radio(*, quiet_label: str | None) -> str:
    """
    Write the radio that follows the app's quiet mode.

    Parameters
    ----------
    quiet_label : str | None
        The radio's label, or None when the app has no quiet mode.

    Returns
    -------
    str
        The radio's lines, the label escaped so it reads as text; empty
        for no label.
    """
    if quiet_label is None:
        return ''
    label = escape(quiet_label).replace('{', '&#123;')
    return QUIET_APP_RADIO.replace(LABEL_MARK, label)


class SettingsPage:
    """
    The Settings page: the device's section first, then the app's.

    The page is drawn as just saved until the status reports the posted
    values, or reports a change made elsewhere.

    Parameters
    ----------
    status : Callable[[], Mapping[str, object]]
        The status fields; `settings` holds the reported form values.
    post : Callable[[object], None]
        Sends each `SettingsPosted` on to the kit.
    plug : SettingsPlug | None
        The app's sections, or None for the device's alone.
    quiet_label : str | None
        The label of the app's quiet mode, or None to offer only My
        hours.
    nav : Sequence[NavItem]
        The page's nav links, in order.
    sheets : Sequence[str]
        Extra stylesheets the page links straight after `/live.css`,
        before the plug's own.
    """

    def __init__(  # pylint: disable=too-many-arguments
        self,
        *,
        status: Callable[[], Mapping[str, object]],
        post: Callable[[object], None],
        plug: SettingsPlug | None,
        quiet_label: str | None,
        nav: Sequence[NavItem] = KIT_NAV,
        sheets: Sequence[str] = (),
    ) -> None:
        self._status = status
        self._post = post
        self._plug = plug
        self._quiet_label = quiet_label
        self._nav = tuple(nav)
        self._sheets = (*sheets, *(plug.sheets if plug else ()))
        self._switches = frozenset(DEVICE_SWITCHES) | (
            plug.switches if plug else frozenset()
        )
        self._radios = frozenset(DEVICE_RADIOS) | (
            plug.radios if plug else frozenset()
        )
        self._errors = {**ERRORS, **(plug.errors if plug else {})}
        self._posted: dict[str, str] = {}
        self._before: dict[str, str] = {}

    def routes(self) -> list[web.RouteDef]:
        """
        List the page's routes.

        Returns
        -------
        list[web.RouteDef]
            The page, its script and its post, in that order.
        """
        return [
            web.get('/settings', self._page),
            web.get('/settings.js', self._script),
            web.post('/settings', self._settings),
        ]

    def _reported(self) -> dict[str, str]:
        """
        Read the form values the status last reported, over the defaults.

        Returns
        -------
        dict[str, str]
            The device's values, then the app's.
        """
        status = self._status()
        values = device_reported(status=status)
        if self._plug is not None:
            values.update(self._plug.values(status=status))
        return values

    def _template(self) -> str:
        """
        Read the page with its head, nav and sections drawn in.

        Returns
        -------
        str
            The page, its field marks still unfilled.
        """
        page = page_frame(
            name='settings.html',
            nav=self._nav,
            current='/settings',
            sheets=self._sheets,
        )
        page = line_filled(
            page=page,
            mark=QUIET_APP_MARK,
            lines=quiet_app_radio(quiet_label=self._quiet_label),
        )
        return line_filled(
            page=page,
            mark=APP_SECTIONS_MARK,
            lines=self._plug.sections() if self._plug else '',
        )

    async def _page(self, request: web.Request) -> web.Response:
        """
        Serve the page, drawn as just saved or else as reported.

        Parameters
        ----------
        request : web.Request
            The incoming request; `error` names a section in error.

        Returns
        -------
        web.Response
            The page.
        """
        status = self._reported()
        if self._posted.items() <= status.items() or status != self._before:
            self._posted = {}
        page = page_text(
            template=self._template(),
            values={**status, **self._posted},
            error=request.query.get('error'),
            switches=self._switches,
            radios=self._radios,
            errors=self._errors,
        )
        return web.Response(text=page, content_type='text/html')

    async def _script(self, request: web.Request) -> web.Response:
        """
        Serve the page's script.

        Parameters
        ----------
        request : web.Request
            The incoming request.

        Returns
        -------
        web.Response
            The script.
        """
        del request
        return web.Response(
            text=shell_text(name='settings.js'),
            content_type='text/javascript',
        )

    def _event(self, *, fields: Mapping[str, str]) -> SettingsPosted:
        """
        Read a post as one event.

        Parameters
        ----------
        fields : Mapping[str, str]
            The posted fields.

        Returns
        -------
        SettingsPosted
            The device's change and the app's.

        Raises
        ------
        UnknownValue
            When a field holds a value the page never sends.
        BadInput
            When a typed value is not usable.
        """
        device = device_change_from(
            fields=fields, quiet_label=self._quiet_label,
        )
        app = (
            self._plug.change_from(fields=fields)
            if self._plug is not None
            else None
        )
        return SettingsPosted(device=device, app=app)

    def _posted_values(
        self, *, fields: Mapping[str, str], event: SettingsPosted,
    ) -> dict[str, str]:
        """
        Write the posted fields the way the status will report them.

        Parameters
        ----------
        fields : Mapping[str, str]
            The posted fields.
        event : SettingsPosted
            The event read from them.

        Returns
        -------
        dict[str, str]
            The device's posted values, then the app's.
        """
        values = device_posted_values(fields=fields, change=event.device)
        if self._plug is not None and event.app is not None:
            values.update(
                self._plug.posted_values(fields=fields, change=event.app),
            )
        return values

    async def _settings(self, request: web.Request) -> web.Response:
        """
        Post a change from the browser.

        Parameters
        ----------
        request : web.Request
            The incoming request.

        Returns
        -------
        web.Response
            400 for an unknown value. For a value that is not usable,
            422 with the error text to the script's JSON. Otherwise 204
            to the script's JSON.

        Raises
        ------
        web.HTTPSeeOther
            To a plain form: back to the page, or to the page's error
            line for a value that is not usable.
        """
        fields = await form_fields(request=request)
        as_json = request.content_type == 'application/json'
        try:
            event = self._event(fields=fields)
        except UnknownValue:
            return web.Response(status=HTTPStatus.BAD_REQUEST)
        except BadInput as error:
            if as_json:
                return web.json_response(
                    {'error': self._errors[error.section]},
                    status=HTTPStatus.UNPROCESSABLE_ENTITY,
                )
            raise web.HTTPSeeOther(
                location=f'/settings?error={error.section}',
            ) from error
        self._before = self._reported()
        self._posted = self._posted_values(fields=fields, event=event)
        self._post(event)
        if as_json:
            return web.Response(status=HTTPStatus.NO_CONTENT)
        raise web.HTTPSeeOther(location='/settings')
