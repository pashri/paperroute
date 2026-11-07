"""The static web files and stylesheet rules keep today's form."""

import asyncio
import json
from collections.abc import Sequence
from hashlib import sha256
from typing import Final

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from paperroute_testkit.device_fakes import FakeKeys
from paperroute_testkit.device_pages import KEY, STATUS
from tests.css_rules import css_rules, rule_line
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


def served_rules(*, app: web.Application) -> list[str]:
    """
    Read the served stylesheet as one line per rule.

    Parameters
    ----------
    app : web.Application
        The app serving `/live.css`.

    Returns
    -------
    list[str]
        Each rule of `/live.css`, in order.
    """
    css = asyncio.run(body(app=app, path='/live.css'))
    return [rule_line(rule=rule) for rule in css_rules(css=css)]


def test_static_responses_match_baseline() -> None:
    """Every static path answers with today's status, headers and bytes."""
    app = display(status=STATUS, keys=FakeKeys(key=KEY)).app()
    records = asyncio.run(fetched(app=app, paths=STATIC))
    text = json.dumps(records, indent=2, sort_keys=True) + '\n'
    check_text(path=GOLDEN / 'assets.json', text=text)


def test_css_rules_match_baseline() -> None:
    """The served stylesheet holds today's rules, in order."""
    app = display(status=STATUS, keys=FakeKeys(key=KEY)).app()
    lines = served_rules(app=app)
    check_text(path=GOLDEN / 'css_rules.txt', text='\n'.join(lines) + '\n')
