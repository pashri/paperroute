"""The static web files and stylesheet rules keep today's form."""

import asyncio
import json
import re
from collections.abc import Sequence
from hashlib import sha256
from pathlib import Path
from typing import Final

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from paperroute_device.adapters.web_frame import SHELL_WEB
from paperroute_device_testkit.device_fakes import FakeKeys
from paperroute_device_testkit.device_pages import KEY, STATUS
from paperroute_device_testkit.paths import ROOT
from tests.css_rules import (
    CssRule,
    css_rules,
    markup_tokens,
    rule_line,
    selector_tokens,
    top_level_split,
    web_files,
)
from tests.golden.scenes import GOLDEN
from tests.golden.test_golden_pages import check_text, display

Client = TestClient[web.Request, web.Application]
STATIC: Final[tuple[str, ...]] = (
    '/',
    '/live.css',
    '/board.css',
    '/palette.css',
    '/live.js',
    '/fonts/public-sans.woff2',
    '/vendor/tom-select.css',
    '/vendor/tom-select.complete.min.js',
    '/vendor/nope.js',
    '/vendor/..%2Fdevice.html',
    '/settings.js',
    '/device.js',
    '/setup.js',
    '/pins.js',
    '/windows.js',
    '/route-colours.css',
    '/stop-search.js',
    '/ways.js',
    '/suggest.js',
)
KIT_PAGES: Final[tuple[Path, ...]] = tuple(
    ROOT / 'packages/paperroute-device/src/paperroute_device/adapters' / name
    for name in (
        'web_device.py', 'device_page.py', 'web_live.py', 'web_frame.py',
    )
)
SHEETS: Final[tuple[str, ...]] = ('/live.css', '/palette.css', '/board.css')
SETTINGS_BORROWS: Final[frozenset[str]] = frozenset({
    '.switch', '.times', '.error',
})
DARK: Final[str] = '@media (prefers-color-scheme: dark)'
SYDNEY_LIGHT: Final[dict[str, str]] = {
    '--masthead': '#002664',
    '--navy': '#002664',
    '--blue': '#146cfd',
    '--focus': '#146cfd',
    '--red': '#d7153a',
    '--rule-1': '#f99d1c',
    '--rule-2': '#0098cd',
    '--rule-3': '#168388',
    '--rule-4': '#00b5ef',
    '--rule-5': '#ee343f',
    '--mode-other': '#646974',
}
SYDNEY_DARK: Final[dict[str, str]] = {
    **SYDNEY_LIGHT,
    '--blue': '#5d9bff',
    '--focus': '#5d9bff',
}
ATTRIBUTE_VALUE: Final[re.Pattern[str]] = re.compile(
    r"""\[[\w-]+[~|^$*]?=\s*["']?([^"'\]]+)""",
)


async def fetched(
    *, app: web.Application, paths: Sequence[str],
) -> dict[str, dict[str, object]]:
    """
    Get each path and record its status, headers and body hash.

    Parameters
    ----------
    app : web.Application
        The app serving the paths.
    paths : Sequence[str]
        Paths to get, without following redirects.

    Returns
    -------
    dict[str, dict[str, object]]
        Status, Content-Type, Cache-Control and sha256 by path.
    """
    client: Client = TestClient(TestServer(app))
    await client.start_server()
    records: dict[str, dict[str, object]] = {}
    try:
        for path in paths:
            response = await client.get(path, allow_redirects=False)
            records[path] = {
                'status': response.status,
                'content_type': response.headers.get('Content-Type'),
                'cache_control': response.headers.get('Cache-Control'),
                'sha256': sha256(await response.read()).hexdigest(),
            }
    finally:
        await client.close()
    return records


async def body(*, app: web.Application, path: str) -> str:
    """
    Get one path's body as text.

    Parameters
    ----------
    app : web.Application
        The app serving the path.
    path : str
        The path.

    Returns
    -------
    str
        The response body.
    """
    client: Client = TestClient(TestServer(app))
    await client.start_server()
    try:
        response = await client.get(path)
        return await response.text()
    finally:
        await client.close()


def served_rules(*, app: web.Application) -> dict[str, list[CssRule]]:
    """
    Read the served stylesheets in load order.

    Parameters
    ----------
    app : web.Application
        The app serving `/live.css`, `/palette.css` and `/board.css`.

    Returns
    -------
    dict[str, list[CssRule]]
        Each sheet's rules, in order, by path, in the order pages load
        them.
    """
    texts = asyncio.run(bodies(app=app))
    return {path: css_rules(css=texts[path]) for path in SHEETS}


async def bodies(*, app: web.Application) -> dict[str, str]:
    """
    Get the stylesheets' text from one running app.

    Parameters
    ----------
    app : web.Application
        The app serving the stylesheets.

    Returns
    -------
    dict[str, str]
        The text of each of `SHEETS`, by path.
    """
    client: Client = TestClient(TestServer(app))
    await client.start_server()
    try:
        return {
            path: await (await client.get(path)).text()
            for path in SHEETS
        }
    finally:
        await client.close()


def resolved(*, rules: Sequence[CssRule], dark: bool) -> dict[str, str]:
    """
    Resolve the custom properties that `:root` rules set, in order.

    Parameters
    ----------
    rules : Sequence[CssRule]
        Rules in cascade order.
    dark : bool
        Whether the dark colour scheme's `:root` rules apply too.

    Returns
    -------
    dict[str, str]
        The last value each custom property is given.
    """
    contexts = {'', DARK} if dark else {''}
    return {
        name.strip(): value.strip()
        for rule in rules
        if rule.selector == ':root' and rule.context in contexts
        for name, value in (line.split(':', 1) for line in rule.body)
        if name.startswith('--')
    }


def kit_text() -> str:
    """
    Read every kit web file and markup-building module as one text.

    Returns
    -------
    str
        The kit's `.html` and `.js` files and page modules, joined.
    """
    files = [
        entry.read_text(encoding='utf-8')
        for entry in web_files(root=SHELL_WEB)
        if entry.name.endswith(('.html', '.js'))
    ]
    modules = [path.read_text(encoding='utf-8') for path in KIT_PAGES]
    return '\n'.join((*files, *modules))


def test_static_responses_match_baseline() -> None:
    """Every static path answers with today's status, headers and bytes."""
    app = display(status=STATUS, keys=FakeKeys(key=KEY)).app()
    records = asyncio.run(fetched(app=app, paths=STATIC))
    text = json.dumps(records, indent=2, sort_keys=True) + '\n'
    check_text(path=GOLDEN / 'assets.json', text=text)


def test_css_rules_match_baseline() -> None:
    """The served sheets' rules, in load order, match the baseline."""
    app = display(status=STATUS, keys=FakeKeys(key=KEY)).app()
    lines = [
        rule_line(rule=rule)
        for rules in served_rules(app=app).values()
        for rule in rules
    ]
    check_text(path=GOLDEN / 'css_rules.txt', text='\n'.join(lines) + '\n')


def test_sydney_palette_resolves_as_before() -> None:
    """The kit's tokens, then Sydney's sheet, give today's colours."""
    app = display(status=STATUS, keys=FakeKeys(key=KEY)).app()
    sheets = served_rules(app=app)
    rules = [*sheets['/live.css'], *sheets['/palette.css']]
    light = resolved(rules=rules, dark=False)
    dark = resolved(rules=rules, dark=True)
    assert {name: light[name] for name in SYDNEY_LIGHT} == SYDNEY_LIGHT
    assert {name: dark[name] for name in SYDNEY_DARK} == SYDNEY_DARK


def test_kit_alone_defines_every_token() -> None:
    """A page with no city sheet still has every palette token."""
    app = display(status=STATUS, keys=FakeKeys(key=KEY)).app()
    kit = resolved(rules=served_rules(app=app)['/live.css'], dark=False)
    assert not any(name.startswith('--mode-') for name in kit)
    assert not [
        name
        for name in SYDNEY_LIGHT
        if not name.startswith('--mode-') and name not in kit
    ]


def test_kit_focus_is_not_sydneys() -> None:
    """The kit draws focus in its own blue, in light and dark."""
    app = display(status=STATUS, keys=FakeKeys(key=KEY)).app()
    kit = served_rules(app=app)['/live.css']
    light = resolved(rules=kit, dark=False)['--focus']
    dark = resolved(rules=kit, dark=True)['--focus']
    assert (light, dark) == ('#2f6db5', '#8ab4f8')


def test_palette_rules_never_match_kit_markup() -> None:
    """Each palette rule is `:root` or names something no kit page has."""
    app = display(status=STATUS, keys=FakeKeys(key=KEY)).app()
    tokens = markup_tokens(root=SHELL_WEB, python=KIT_PAGES)
    text = kit_text()
    palette = served_rules(app=app)['/palette.css']
    matching = [
        selector
        for rule in palette
        for selector in top_level_split(text=rule.selector)
        if selector != ':root'
        and selector_tokens(selector=selector) <= tokens
        and all(
            value in text
            for value in ATTRIBUTE_VALUE.findall(selector)
        )
    ]
    assert palette
    assert not matching


def test_kit_markup_needs_no_board_rule() -> None:
    """
    Every board selector but `:root` names a class or id kit lacks.

    The one exception is the kit Settings page's Device section, whose
    switches, times and error line are styled by the board's rules.
    """
    app = display(status=STATUS, keys=FakeKeys(key=KEY)).app()
    tokens = markup_tokens(root=SHELL_WEB, python=KIT_PAGES)
    matching = {
        selector
        for rule in served_rules(app=app)['/board.css']
        for selector in top_level_split(text=rule.selector)
        if selector != ':root'
        and selector_tokens(selector=selector) <= tokens
    }
    assert matching <= SETTINGS_BORROWS
