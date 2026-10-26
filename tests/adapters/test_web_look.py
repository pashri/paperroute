"""Tests for the web pages' look: font, tokens, nav, cards and buttons."""

import hashlib
import re
from collections.abc import AsyncIterator
from http import HTTPStatus
from importlib.resources import files
from typing import Final

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from paperroute.adapters.web_display import WebDisplay

Client = TestClient[web.Request, web.Application]
FONT_SHA256: Final[str] = (
    'c1b6da516e0062e9c2f341b3a51dd2d621d946da72f06c6cfe05fd9d2dd8622d'
)
WEB: Final = files('paperroute') / 'assets' / 'web'


def css_text() -> str:
    """
    Read the packaged stylesheet.

    Returns
    -------
    str
        The text of `live.css`.
    """
    return (WEB / 'live.css').read_text(encoding='utf-8')


def block_after(*, css: str, start: int) -> str:
    """
    Return the brace-balanced block that opens at or after `start`.

    Parameters
    ----------
    css : str
        The stylesheet.
    start : int
        Where to look for the opening brace.

    Returns
    -------
    str
        The text between the braces.
    """
    opening = css.index('{', start)
    depth = 0
    for at in range(opening, len(css)):
        depth += {'{': 1, '}': -1}.get(css[at], 0)
        if depth == 0:
            return css[opening + 1:at]
    return css[opening + 1:]


def dark_block(*, css: str) -> str:
    """
    Return the declarations of `:root` in the dark colour scheme block.

    Parameters
    ----------
    css : str
        The stylesheet.

    Returns
    -------
    str
        The body of the `:root` rule inside the dark media block.
    """
    media = block_after(
        css=css,
        start=css.index('@media (prefers-color-scheme: dark)'),
    )
    return block_after(css=media, start=media.index(':root'))


def declarations(*, body: str) -> list[str]:
    """
    Split a rule body into declarations.

    Parameters
    ----------
    body : str
        The text between a rule's braces.

    Returns
    -------
    list[str]
        Each non-empty declaration, trimmed.
    """
    return [part.strip() for part in body.split(';') if part.strip()]


@pytest.fixture(name='client')
async def _client() -> AsyncIterator[Client]:
    """A client of a display with no device pages."""
    client = TestClient(TestServer(WebDisplay().app()))
    await client.start_server()
    yield client
    await client.close()


async def test_font_is_served(client: Client) -> None:
    """The vendored font is served as woff2 and its licence is packaged."""
    response = await client.get('/fonts/public-sans.woff2')
    body = await response.read()
    assert response.status == HTTPStatus.OK
    assert response.content_type == 'font/woff2'
    assert response.headers['Cache-Control'] == 'max-age=86400'
    assert body.startswith(b'wOF2')
    assert hashlib.sha256(body).hexdigest() == FONT_SHA256
    licence = (WEB / 'fonts' / 'OFL.txt').read_text(encoding='utf-8')
    assert 'Public Sans' in licence


def test_tokens_on_root() -> None:
    """The NSW colours are tokens on the first `:root`, beside the font."""
    css = css_text()
    root = declarations(body=block_after(css=css, start=css.index(':root')))
    for token in ('--navy: #002664', '--blue: #146cfd', '--red: #d7153a'):
        assert token in root
    face = block_after(css=css, start=css.index('@font-face'))
    assert "url('/fonts/public-sans.woff2')" in face


def test_dark_block_only_sets_tokens() -> None:
    """The dark scheme redefines tokens and the colour scheme only."""
    body = dark_block(css=css_text())
    found = declarations(body=body)
    assert 'color-scheme: dark' in found
    assert all(
        line.startswith('--') or line == 'color-scheme: dark'
        for line in found
    )


def test_font_sizes_are_rem() -> None:
    """No font size is set in pixels."""
    sizes = re.findall(r'font-size:\s*([^;]+);', css_text())
    assert sizes
    assert not [size for size in sizes if 'px' in size]


def test_focus_and_motion() -> None:
    """Focus is drawn in the focus token and motion can be turned off."""
    css = css_text()
    focus = block_after(css=css, start=css.index(':focus-visible'))
    assert 'var(--focus)' in focus
    assert '@media (prefers-reduced-motion: reduce)' in css
    assert '--navy' in css


def test_no_framework() -> None:
    """No page asset pulls in Bootstrap."""
    for item in WEB.iterdir():
        if item.is_file():
            assert 'bootstrap' not in item.read_bytes().decode(
                encoding='utf-8',
                errors='replace',
            ).casefold(), item.name
