"""The static web files and stylesheet rules keep today's form."""

import asyncio
import json
from collections.abc import Sequence
from hashlib import sha256
from pathlib import Path
from typing import Final

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from paperroute_device.adapters.web_frame import SHELL_WEB
from paperroute_testkit.device_fakes import FakeKeys
from paperroute_testkit.device_pages import KEY, STATUS
from paperroute_testkit.paths import ROOT
from tests.css_rules import (
    CssRule,
    css_rules,
    markup_tokens,
    rule_line,
    selector_tokens,
    specificity,
    top_level_split,
)
from tests.golden.scenes import GOLDEN
from tests.golden.test_golden_pages import check_text, display

Client = TestClient[web.Request, web.Application]
STATIC: Final[tuple[str, ...]] = (
    '/',
    '/live.css',
    '/board.css',
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
SKIP_SCHOOL: Final[str] = (
    'form:has(input[name="skip_school_buses"]:not(:checked)) '
    '.switch:has(input[name="skip_school_on_route_pins"])'
)
NOT_A_SWITCH: Final[dict[str, str]] = {
    'fieldset': 'a toggle switch is never a fieldset',
    '.password': 'the Wi-Fi password box is not a switch',
    '.facts': "the Device page's facts list holds no switch",
    '.facts dt': 'a facts term is not a switch',
    '.downloads': "the Device page's download list holds no switch",
    'dialog': 'a switch is never a dialog',
}
REVIEWED_PAIRS: Final[dict[tuple[str, str], str]] = {
    ('.switch', 'fieldset'): NOT_A_SWITCH['fieldset'],
    ('.switch', '.password'): NOT_A_SWITCH['.password'],
    ('.switch', '.facts'): NOT_A_SWITCH['.facts'],
    ('.switch', '.downloads'): NOT_A_SWITCH['.downloads'],
    (SKIP_SCHOOL, '.facts dt'): NOT_A_SWITCH['.facts dt'],
    (SKIP_SCHOOL, 'dialog'): NOT_A_SWITCH['dialog'],
}


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


def sheets(*, app: web.Application) -> tuple[list[CssRule], list[CssRule]]:
    """
    Read the shell's and the board's served stylesheets.

    Parameters
    ----------
    app : web.Application
        The app serving `/live.css` and `/board.css`.

    Returns
    -------
    tuple[list[CssRule], list[CssRule]]
        The rules of `/live.css`, then those of `/board.css`, each in
        order.
    """
    shell, board = asyncio.run(bodies(app=app))
    return css_rules(css=shell), css_rules(css=board)


async def bodies(*, app: web.Application) -> tuple[str, str]:
    """
    Get the two stylesheets' text from one running app.

    Parameters
    ----------
    app : web.Application
        The app serving `/live.css` and `/board.css`.

    Returns
    -------
    tuple[str, str]
        The text of `/live.css`, then of `/board.css`.
    """
    client: Client = TestClient(TestServer(app))
    await client.start_server()
    try:
        shell = await (await client.get('/live.css')).text()
        board = await (await client.get('/board.css')).text()
    finally:
        await client.close()
    return shell, board


def is_subsequence(*, part: Sequence[str], whole: Sequence[str]) -> bool:
    """
    Check that `part` appears in `whole` in the same order.

    Parameters
    ----------
    part : Sequence[str]
        The shorter list.
    whole : Sequence[str]
        The longer list.

    Returns
    -------
    bool
        Whether every item of `part` appears in `whole`, in order.
    """
    items = iter(whole)
    return all(item in items for item in part)


def properties(*, rule: CssRule) -> dict[str, bool]:
    """
    Read the properties a rule sets.

    Parameters
    ----------
    rule : CssRule
        The rule.

    Returns
    -------
    dict[str, bool]
        Whether each property is `!important`, by property name.
    """
    return {
        line.split(':', 1)[0].strip(): '!important' in line
        for line in rule.body
    }


def overriding_pairs(
    *, board: CssRule, shell: CssRule,
) -> set[tuple[str, str]]:
    """
    Find where a board rule loaded later could now override a shell rule.

    Parameters
    ----------
    board : CssRule
        A board rule that came before `shell` in the single sheet.
    shell : CssRule
        A shell rule.

    Returns
    -------
    set[tuple[str, str]]
        Each (board selector, shell selector) pair where the board
        selector is at least as specific; empty when the rules sit in
        different `@media` blocks or share no property that the shell
        rule does not set as `!important`.
    """
    shell_properties = properties(rule=shell)
    shared = {
        name
        for name in properties(rule=board)
        if name in shell_properties and not shell_properties[name]
    }
    if board.context != shell.context or not shared:
        return set()
    return {
        (later, earlier)
        for later in top_level_split(text=board.selector)
        for earlier in top_level_split(text=shell.selector)
        if specificity(selector=later) >= specificity(selector=earlier)
    }


def test_static_responses_match_baseline() -> None:
    """Every static path answers with today's status, headers and bytes."""
    app = display(status=STATUS, keys=FakeKeys(key=KEY)).app()
    records = asyncio.run(fetched(app=app, paths=STATIC))
    text = json.dumps(records, indent=2, sort_keys=True) + '\n'
    check_text(path=GOLDEN / 'assets.json', text=text)


def baseline() -> list[str]:
    """
    Read the baseline rule lines.

    Returns
    -------
    list[str]
        One line per rule of the single stylesheet, in order.
    """
    return (GOLDEN / 'css_rules.txt').read_text(encoding='utf-8').splitlines()


def test_split_keeps_every_rule() -> None:
    """The two sheets hold the baseline's rules, each in its order."""
    app = display(status=STATUS, keys=FakeKeys(key=KEY)).app()
    shell, board = (
        [rule_line(rule=rule) for rule in rules]
        for rules in sheets(app=app)
    )
    base = baseline()
    assert sorted(shell + board) == sorted(base)
    assert is_subsequence(part=shell, whole=base)
    assert is_subsequence(part=board, whole=base)
    assert board


def test_split_keeps_the_cascade() -> None:
    """A board rule now loaded later overrides only reviewed shell rules."""
    app = display(status=STATUS, keys=FakeKeys(key=KEY)).app()
    shell_rules, board_rules = sheets(app=app)
    board = {rule_line(rule=rule) for rule in board_rules}
    order = baseline()
    by_line = {
        rule_line(rule=rule): rule
        for rule in [*shell_rules, *board_rules]
    }
    flagged = {
        pair
        for index, line in enumerate(order)
        if line in board
        for later in order[index + 1:]
        if later not in board
        for pair in overriding_pairs(
            board=by_line[line], shell=by_line[later],
        )
    }
    assert flagged == set(REVIEWED_PAIRS)


def test_kit_markup_needs_no_board_rule() -> None:
    """Every board selector names a class or id no kit markup carries."""
    app = display(status=STATUS, keys=FakeKeys(key=KEY)).app()
    tokens = markup_tokens(root=SHELL_WEB, python=KIT_PAGES)
    matching = [
        selector
        for rule in sheets(app=app)[1]
        for selector in top_level_split(text=rule.selector)
        if selector_tokens(selector=selector) <= tokens
    ]
    assert not matching
