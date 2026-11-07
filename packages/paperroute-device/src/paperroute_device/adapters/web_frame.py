"""The web shell's own files, shipped with the device kit, and its nav."""

import html
from collections.abc import Sequence
from dataclasses import dataclass
from importlib.resources import files
from importlib.resources.abc import Traversable
from typing import Final

SHELL_WEB: Final[Traversable] = files('paperroute_device') / 'web'
NAV_MARK: Final[str] = '{{nav}}'
NAV_ITEM: Final[str] = """      <li>
        <a href="{href}"{mark}>
          <svg aria-hidden="true" focusable="false" viewBox="0 0 24 24">
            {icon}
          </svg>
          <span>{label}</span>
        </a>
      </li>"""


@dataclass(frozen=True)
class NavItem:
    """
    One link in the pages' nav.

    Attributes
    ----------
    href : str
        Where the link goes.
    label : str
        The link's text.
    icon : str
        The inner markup of the link's SVG icon; trusted, not escaped.
    """

    href: str
    label: str
    icon: str


LIVE_NAV: Final[NavItem] = NavItem(
    href='/',
    label='Live',
    icon='<rect x="3" y="5" width="18" height="12" rx="1"/>'
    '<path d="M8 21h8"/>',
)
DEVICE_NAV: Final[NavItem] = NavItem(
    href='/device',
    label='Device',
    icon='<rect x="6" y="3" width="12" height="18" rx="2"/>'
    '<path d="M10 18h4"/>',
)
KIT_NAV: Final[tuple[NavItem, ...]] = (LIVE_NAV, DEVICE_NAV)


def shell_path(*, name: str) -> Traversable:
    """
    Find one of the shell's files.

    Parameters
    ----------
    name : str
        Path within the shell's web directory, such as
        `fonts/public-sans.woff2`.

    Returns
    -------
    Traversable
        The file.
    """
    return SHELL_WEB.joinpath(*name.split('/'))


def shell_text(*, name: str) -> str:
    """
    Read one of the shell's files as text.

    Parameters
    ----------
    name : str
        Path within the shell's web directory.

    Returns
    -------
    str
        The file contents.
    """
    return shell_path(name=name).read_text(encoding='utf-8')


def shell_bytes(*, name: str) -> bytes:
    """
    Read one of the shell's files as bytes.

    Parameters
    ----------
    name : str
        Path within the shell's web directory, such as
        `fonts/public-sans.woff2`.

    Returns
    -------
    bytes
        The file contents.
    """
    return shell_path(name=name).read_bytes()


def nav_html(*, items: Sequence[NavItem], current: str | None) -> str:
    """
    Draw the pages' nav.

    Parameters
    ----------
    items : Sequence[NavItem]
        The links, in order.
    current : str | None
        The href of the open page, marked as current; None marks none.

    Returns
    -------
    str
        The `<nav>` element, with no indent on its first line and no
        trailing newline.
    """
    links = (
        NAV_ITEM.format(
            href=html.escape(item.href),
            mark=' aria-current="page"' if item.href == current else '',
            icon=item.icon,
            label=html.escape(item.label),
        )
        for item in items
    )
    return '\n'.join((
        '<nav aria-label="Pages">',
        '    <ul class="pages">',
        *links,
        '    </ul>',
        '  </nav>',
    ))


def page_frame(
    *, name: str, nav: Sequence[NavItem], current: str | None
) -> str:
    """
    Read one of the shell's pages with its nav drawn in.

    Parameters
    ----------
    name : str
        The page's file name within the shell's web directory.
    nav : Sequence[NavItem]
        The nav's links, in order.
    current : str | None
        The href of the open page; None marks none.

    Returns
    -------
    str
        The page, its other marks still unfilled.
    """
    return shell_text(name=name).replace(
        NAV_MARK, nav_html(items=nav, current=current), 1,
    )
