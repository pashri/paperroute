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

from paperroute.adapters.web_board import SYDNEY_NAV
from paperroute.adapters.web_display import WebDisplay
from paperroute_device.adapters.web_frame import SHELL_WEB, page_frame
from paperroute_testkit.page_markup import (
    Node,
    by_id,
    inside,
    parse_page,
    parse_tree,
)
from tests.adapters.test_web_device import SHELL_PAGES, sydney_site

Client = TestClient[web.Request, web.Application]
FONT_SHA256: Final[str] = (
    'c1b6da516e0062e9c2f341b3a51dd2d621d946da72f06c6cfe05fd9d2dd8622d'
)
TOM_SELECT_JS_SHA256: Final[str] = (
    'b797005cf23378fb38448b80dc57a33313a5c577b841765743bb1f289c4d53e6'
)
TOM_SELECT_CSS_SHA256: Final[str] = (
    '7b29f39e2916acfb3d050e00df8fcdfca89279ead2d8e654afec307ca63b10f9'
)
WEB: Final = files('paperroute') / 'assets' / 'web'
PAGES: Final[list[tuple[str, str | None]]] = [
    ('live.html', '/'),
    ('pins.html', '/pins'),
    ('pin-edit.html', '/pins'),
    ('settings.html', '/settings'),
    ('device.html', '/device'),
    ('power.html', '/device'),
    ('setup.html', None),
]
DANGER: Final[frozenset[str]] = frozenset(
    {'Delete', 'Shut down', 'Reset settings', 'Erase everything'},
)
SECONDARY: Final[frozenset[str]] = frozenset({
    '↑', '↓', 'Edit', 'Cancel', 'Show choices', 'Add window', 'Remove',
    'Show password', 'Join another network…', '⟲ Left', 'Right ⟳',
    'Add a connection',
})
MAIN: Final[frozenset[str]] = frozenset(
    {'Search', 'Save', 'Save key', 'Join', 'Press'},
)
CHECKED_ELSEWHERE: Final[frozenset[str]] = frozenset(
    {'Restart', '{{confirm}}'},
)
CARD_PAGES: Final[list[str]] = [name for name, _ in PAGES]
NAV_LINKS: Final[list[tuple[str, str]]] = [
    ('/', 'Live'),
    ('/pins', 'Pins'),
    ('/settings', 'Settings'),
    ('/device', 'Device'),
]


def css_text() -> str:
    """
    Read the packaged stylesheet.

    Returns
    -------
    str
        The text of `live.css`.
    """
    return (SHELL_WEB / 'live.css').read_text(encoding='utf-8')


def page_text(*, name: str) -> str:
    """
    Read a packaged page.

    Parameters
    ----------
    name : str
        The page's file name.

    Returns
    -------
    str
        The page's HTML; a shell page with Sydney's nav drawn in.
    """
    if name in SHELL_PAGES:
        return page_frame(
            name=name, nav=SYDNEY_NAV, current=dict(PAGES)[name],
        )
    return (WEB / name).read_text(encoding='utf-8')


def nav_html(*, text: str) -> str:
    """
    Return a page's nav element.

    Parameters
    ----------
    text : str
        The page's HTML.

    Returns
    -------
    str
        The `<nav>` element, from its opening to its closing tag.
    """
    found = re.search(r'<nav\b.*?</nav>', text, flags=re.DOTALL)
    assert found is not None
    return found.group(0)


def outside_media(*, css: str) -> str:
    """
    Remove every `@media` block from a stylesheet.

    Parameters
    ----------
    css : str
        The stylesheet.

    Returns
    -------
    str
        The rules that apply at every width and scheme.
    """
    while '@media' in css:
        start = css.index('@media')
        body = block_after(css=css, start=start)
        end = css.index(body, start) + len(body) + 1
        css = css[:start] + css[end:]
    return css


def rule_body(*, css: str, selector: str) -> str:
    """
    Return the body of the rule whose selector list is exactly `selector`.

    Parameters
    ----------
    css : str
        The stylesheet.
    selector : str
        The rule's whole selector.

    Returns
    -------
    str
        The text between the rule's braces.
    """
    found = re.search(
        rf'(?:^|}}|;)\s*{re.escape(selector)}\s*{{',
        css,
        flags=re.MULTILINE,
    )
    assert found is not None, selector
    return block_after(css=css, start=found.end() - 1)


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
    licence = (SHELL_WEB / 'fonts' / 'OFL.txt').read_text(encoding='utf-8')
    assert 'Public Sans' in licence


@pytest.mark.parametrize(('name', 'kind', 'digest'), [
    (
        'tom-select.complete.min.js',
        'text/javascript',
        TOM_SELECT_JS_SHA256,
    ),
    ('tom-select.css', 'text/css', TOM_SELECT_CSS_SHA256),
])
async def test_tom_select_is_served(
    client: Client,
    name: str,
    kind: str,
    digest: str,
) -> None:
    """Tom Select's script and sheet are served, pinned by SHA-256.

    Its licence is packaged beside them.
    """
    response = await client.get(f'/vendor/{name}')
    body = await response.read()
    assert response.status == HTTPStatus.OK
    assert response.content_type == kind
    assert response.headers['Cache-Control'] == 'max-age=86400'
    assert hashlib.sha256(body).hexdigest() == digest
    licence = (WEB / 'vendor' / 'LICENSE-tom-select.txt').read_text(
        encoding='utf-8',
    )
    assert 'Apache License' in licence
    assert 'Brian Reavis' in licence


async def test_vendor_unknown_name_is_missing(client: Client) -> None:
    """Only the vendored files are served."""
    response = await client.get('/vendor/..%2Fdevice.html')
    assert response.status == HTTPStatus.NOT_FOUND


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
    motion = block_after(
        css=css,
        start=css.index('@media (prefers-reduced-motion: reduce)'),
    )
    calm = declarations(body=rule_body(
        css=motion, selector='*,\n  *::before,\n  *::after',
    ))
    assert 'transition: none !important' in calm
    assert 'animation: none !important' in calm


def test_no_framework() -> None:
    """No page asset pulls in Bootstrap."""
    items = [*WEB.iterdir(), *SHELL_WEB.iterdir()]
    for item in items:
        if item.is_file():
            assert 'bootstrap' not in item.read_bytes().decode(
                encoding='utf-8',
                errors='replace',
            ).casefold(), item.name


@pytest.mark.parametrize(('name', 'current'), PAGES)
def test_one_nav_landmark(name: str, current: str | None) -> None:
    """One labelled nav links the four pages with icons, marking one."""
    page = parse_page(text=page_text(name=name))
    assert page.navs == [{'aria-label': 'Pages'}]
    assert [(href, text) for href, _, text in page.links] == NAV_LINKS
    marked = [href for href, mark, _ in page.links if mark]
    assert marked == ([current] if current else [])
    assert page.nav_icons == 4


@pytest.mark.parametrize(('name', 'current'), PAGES)
def test_nav_is_plain_links(name: str, current: str | None) -> None:
    """The nav holds only links, with no buttons or click handlers."""
    del current
    nav = nav_html(text=page_text(name=name))
    assert '<button' not in nav
    assert 'onclick' not in nav
    assert nav.count('<a href=') == len(NAV_LINKS)


@pytest.mark.parametrize(('name', 'current'), PAGES)
def test_page_basics(name: str, current: str | None) -> None:
    """Each page has one h1, the stylesheet, no inline style and a lang."""
    del current
    text = page_text(name=name)
    page = parse_page(text=text)
    assert page.h1 == ['paperroute']
    assert '/live.css' in page.stylesheets
    assert page.styled == 0
    assert '<html lang="en">' in text


@pytest.mark.parametrize(('name', 'current'), PAGES)
def test_navs_match(name: str, current: str | None) -> None:
    """Every nav is the Live page's nav apart from the current mark."""
    del current
    mark = ' aria-current="page"'
    nav = nav_html(text=page_text(name=name)).replace(mark, '')
    live = nav_html(text=page_text(name='live.html')).replace(mark, '')
    assert nav == live


def test_main_clears_bottom_bar() -> None:
    """On phones the page ends above the bottom bar and the safe area."""
    css = outside_media(css=css_text())
    main = rule_body(css=css, selector='main')
    padding = re.search(r'padding-block-end:\s*([^;]+);', main)
    assert padding is not None
    assert 'var(--bar)' in padding.group(1)
    assert 'env(safe-area-inset-bottom' in padding.group(1)
    nav = rule_body(css=css, selector='body > nav')
    assert re.search(r'min-height:\s*var\(--bar\)', nav)


def test_edges_clear_side_safe_areas() -> None:
    """Header, main and footer keep off a landscape notch on both sides."""
    css = css_text()
    root = declarations(body=block_after(css=css, start=css.index(':root')))
    assert (
        '--edge-start: max(var(--gap), env(safe-area-inset-left, 0px))'
        in root
    )
    assert (
        '--edge-end: max(var(--gap), env(safe-area-inset-right, 0px))'
        in root
    )
    plain = outside_media(css=css)
    for selector in ('header', 'main', 'body > footer'):
        body = declarations(body=rule_body(css=plain, selector=selector))
        assert 'padding-inline: var(--edge-start) var(--edge-end)' in body, (
            selector
        )
    wide = block_after(css=css, start=css.index('@media (min-width: 48em)'))
    nav = declarations(body=rule_body(css=wide, selector='body > nav'))
    assert 'padding-inline-start: env(safe-area-inset-left, 0px)' in nav
    for selector in ('main', 'body > footer'):
        body = declarations(body=rule_body(css=wide, selector=selector))
        assert 'padding-inline-start: var(--gap)' in body, selector


@pytest.mark.parametrize(('name', 'current'), PAGES)
def test_viewport_fit_cover(name: str, current: str | None) -> None:
    """Every page's viewport reaches under the phone's safe areas."""
    del current
    found = re.search(
        r'<meta name="viewport" content="([^"]+)">',
        page_text(name=name),
    )
    assert found is not None
    assert 'viewport-fit=cover' in found.group(1)


@pytest.mark.parametrize('name', CARD_PAGES)
def test_every_section_is_a_card(name: str) -> None:
    """Every section in main is a card with a heading."""
    page = parse_page(text=page_text(name=name))
    assert page.sections
    for classes, headed in page.sections:
        assert 'card' in classes
        assert headed


@pytest.mark.parametrize('name', CARD_PAGES)
def test_button_tones(name: str) -> None:
    """Danger, secondary and main buttons carry the classes Ruling 5 names."""
    for text, classes in parse_page(text=page_text(name=name)).buttons:
        assert not {'danger', 'secondary'} <= classes, text
        if text in CHECKED_ELSEWHERE:
            continue
        if text in DANGER:
            assert 'danger' in classes, text
        elif text in SECONDARY:
            assert 'secondary' in classes, text
        else:
            assert text in MAIN, text
            assert not classes & {'danger', 'secondary'}, text


def test_device_restart_tones() -> None:
    """Restart is secondary on the Device card and main in its dialog."""
    buttons = parse_page(text=page_text(name='device.html')).buttons
    restarts = [classes for text, classes in buttons if text == 'Restart']
    assert restarts == [frozenset({'secondary'}), frozenset()]


@pytest.mark.parametrize(('action', 'danger'), [
    ('poweroff', True),
    ('reboot', False),
])
async def test_power_page_tone(action: str, danger: bool) -> None:
    """Shutting down asks with a danger button; restarting does not."""
    display = WebDisplay(device=sydney_site().pages())
    client = TestClient(TestServer(display.app()))
    await client.start_server()
    try:
        response = await client.get(f'/device/power?action={action}')
        page = parse_page(text=await response.text())
    finally:
        await client.close()
    confirm = page.buttons[-1][1]
    assert ('danger' in confirm) is danger
    assert 'secondary' not in confirm
    assert ('Cancel', frozenset({'button', 'secondary'})) in page.buttons
    assert '{{' not in await response.text()
    assert 'class=""' not in await response.text()


def knob_group(
    *, nodes: list[Node], group: Node,
) -> tuple[str, list[tuple[str, bool]]]:
    """
    Read a knob group's label and buttons.

    Parameters
    ----------
    nodes : list[Node]
        Every element of the page.
    group : Node
        The group.

    Returns
    -------
    tuple[str, list[tuple[str, bool]]]
        The labelling heading's text, and each button's `data-action`
        and whether it is secondary.
    """
    label = by_id(nodes=nodes, ident=group.attrs['aria-labelledby'])
    buttons = [
        node for node in inside(nodes=nodes, outer=group)
        if node.tag == 'button'
    ]
    return label.text.strip(), [
        (
            node.attrs.get('data-action') or '',
            'secondary' in (node.attrs.get('class') or '').split(),
        )
        for node in buttons
    ]


def test_live_has_one_knobs_card() -> None:
    """Live holds the frame, then one Knobs card with Top and Bottom."""
    nodes = parse_tree(text=page_text(name='live.html'))
    sections = [node for node in nodes if node.tag == 'section']
    assert len(sections) == 1
    card = sections[0]
    assert set((card.attrs['class'] or '').split()) == {'card', 'knobs'}
    heading = by_id(nodes=nodes, ident=card.attrs['aria-labelledby'])
    assert (heading.tag, heading.text.strip()) == ('h2', 'Knobs')
    held = inside(nodes=nodes, outer=card)
    groups = [node for node in held if node.attrs.get('role') == 'group']
    knobs = [knob_group(nodes=nodes, group=group) for group in groups]
    wanted = [('left', True), ('press', False), ('right', True)]
    assert knobs == [('Top', wanted), ('Bottom', wanted)]
    hint = by_id(nodes=nodes, ident='hint')
    assert hint in held
    assert hint.index > max(inside(nodes=nodes, outer=groups[-1])).index
    assert by_id(nodes=nodes, ident='frame').index < card.index


def test_on_screen_now_matches_line_size() -> None:
    """'On the screen now' is set in the pin line's small size."""
    body = rule_body(css=css_text(), selector='.pins small,\n.pins .now')
    assert 'font-size: 0.875rem' in declarations(body=body)


def test_live_frame_is_drawn_at_its_own_size() -> None:
    """The panel preview is never shrunk; a narrow page scrolls its box."""
    css = css_text()
    frame = declarations(body=rule_body(css=css, selector='.frame'))
    assert 'image-rendering: pixelated' in frame
    assert 'max-width: 100%' not in frame
    box = declarations(body=rule_body(css=css, selector='.frame-box'))
    assert 'overflow-x: auto' in box
    nodes = parse_tree(text=page_text(name='live.html'))
    holder = next(
        node for node in nodes if 'frame-box' in (node.attrs.get('class') or '')
    )
    assert by_id(nodes=nodes, ident='frame') in inside(
        nodes=nodes, outer=holder,
    )


def test_feed_names_wrap_so_the_times_show() -> None:
    """A long feed name wraps instead of pushing its times out of view."""
    body = rule_body(css=css_text(), selector=".table th[scope='row']")
    assert 'white-space: normal' in declarations(body=body)


def test_hidden_beats_the_button_display() -> None:
    """A `hidden` element stays hidden whatever its own display rule."""
    assert '[hidden] {\n  display: none !important;\n}' in css_text()


def test_danger_zone_rule() -> None:
    """The Delete card's top border is the danger colour."""
    rule = '.danger-zone {\n  border-block-start-color: var(--danger);\n}'
    assert rule in css_text()
