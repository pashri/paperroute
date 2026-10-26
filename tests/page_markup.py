"""Read the parts of a web page that the look tests check."""

from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Final

HEADINGS: Final[frozenset[str]] = frozenset(
    {'h1', 'h2', 'h3', 'h4', 'h5', 'h6'},
)

Attrs = list[tuple[str, str | None]]


@dataclass
class Capture:
    """
    Text being collected for one open element.

    Attributes
    ----------
    tag : str
        The element's tag.
    kind : str
        What the text is for: `link`, `h1` or `button`.
    attrs : dict[str, str | None]
        The element's attributes.
    text : list[str]
        The text seen so far.
    """

    tag: str
    kind: str
    attrs: dict[str, str | None]
    text: list[str] = field(default_factory=list)


@dataclass
class OpenSection:
    """
    A `<section>` not yet closed.

    Attributes
    ----------
    classes : frozenset[str]
        Its class names.
    headed : bool
        Whether a heading has been seen inside it.
    """

    classes: frozenset[str]
    headed: bool = False


def classes_of(*, attrs: dict[str, str | None]) -> frozenset[str]:
    """
    Split an element's class attribute.

    Parameters
    ----------
    attrs : dict[str, str | None]
        The element's attributes.

    Returns
    -------
    frozenset[str]
        The class names.
    """
    return frozenset((attrs.get('class') or '').split())


class PageParser(HTMLParser):
    """
    Collect a page's nav, headings, buttons, sections and stylesheets.

    Attributes
    ----------
    navs : list[dict[str, str | None]]
        Each `<nav>`'s attributes.
    links : list[tuple[str, bool, str]]
        Each link inside a nav: href, marked current, visible text.
    nav_icons : int
        `<svg aria-hidden="true">` elements inside a nav.
    styled : int
        Elements with a `style` attribute.
    stylesheets : list[str]
        Linked stylesheet hrefs, in order.
    h1 : list[str]
        The text of each `<h1>`.
    buttons : list[tuple[str, frozenset[str]]]
        Text and classes of each `<button>` and `a.button`.
    sections : list[tuple[frozenset[str], bool]]
        Classes of each `<section>` in `<main>`, and whether it holds a
        heading.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.navs: list[dict[str, str | None]] = []
        self.links: list[tuple[str, bool, str]] = []
        self.nav_icons = 0
        self.styled = 0
        self.stylesheets: list[str] = []
        self.h1: list[str] = []
        self.buttons: list[tuple[str, frozenset[str]]] = []
        self.sections: list[tuple[frozenset[str], bool]] = []
        self.open_sections: list[OpenSection] = []
        self.captures: list[Capture] = []
        self.in_nav = 0
        self.in_main = 0

    def handle_starttag(self, tag: str, attrs: Attrs) -> None:
        """
        Note an opening tag.

        Parameters
        ----------
        tag : str
            The tag.
        attrs : Attrs
            Its attributes.
        """
        found = dict(attrs)
        if 'style' in found:
            self.styled += 1
        if tag == 'link' and found.get('rel') == 'stylesheet':
            self.stylesheets.append(found.get('href') or '')
        self._landmark(tag=tag, attrs=found)
        self._capture(tag=tag, attrs=found)

    def _landmark(self, *, tag: str, attrs: dict[str, str | None]) -> None:
        """
        Track navs, main, sections, headings and nav icons.

        Parameters
        ----------
        tag : str
            The tag.
        attrs : dict[str, str | None]
            Its attributes.
        """
        if tag == 'nav':
            self.navs.append(attrs)
            self.in_nav += 1
        elif tag == 'main':
            self.in_main += 1
        elif tag == 'section' and self.in_main:
            self.open_sections.append(
                OpenSection(classes=classes_of(attrs=attrs)),
            )
        elif tag in HEADINGS:
            for opened in self.open_sections:
                opened.headed = True
        if tag == 'svg' and self.in_nav and attrs.get('aria-hidden') == 'true':
            self.nav_icons += 1

    def _capture(self, *, tag: str, attrs: dict[str, str | None]) -> None:
        """
        Start collecting text for links, `<h1>`s and buttons.

        Parameters
        ----------
        tag : str
            The tag.
        attrs : dict[str, str | None]
            Its attributes.
        """
        if tag == 'a' and self.in_nav:
            self.captures.append(Capture(tag=tag, kind='link', attrs=attrs))
        elif tag == 'h1':
            self.captures.append(Capture(tag=tag, kind='h1', attrs=attrs))
        if tag == 'button' or (
            tag == 'a' and 'button' in classes_of(attrs=attrs)
        ):
            self.captures.append(Capture(tag=tag, kind='button', attrs=attrs))

    def handle_endtag(self, tag: str) -> None:
        """
        Note a closing tag.

        Parameters
        ----------
        tag : str
            The tag.
        """
        if tag == 'nav':
            self.in_nav -= 1
        elif tag == 'main':
            self.in_main -= 1
        elif tag == 'section' and self.open_sections:
            closed = self.open_sections.pop()
            self.sections.append((closed.classes, closed.headed))
        for capture in [c for c in self.captures if c.tag == tag]:
            self.captures.remove(capture)
            self._finish(capture=capture)

    def _finish(self, *, capture: Capture) -> None:
        """
        Record a closed element's text.

        Parameters
        ----------
        capture : Capture
            The element and its text.
        """
        text = ' '.join(''.join(capture.text).split())
        if capture.kind == 'link':
            current = capture.attrs.get('aria-current') == 'page'
            href = capture.attrs.get('href') or ''
            self.links.append((href, current, text))
        elif capture.kind == 'h1':
            self.h1.append(text)
        else:
            self.buttons.append((text, classes_of(attrs=capture.attrs)))

    def handle_data(self, data: str) -> None:
        """
        Add text to every element being collected.

        Parameters
        ----------
        data : str
            The text.
        """
        for capture in self.captures:
            capture.text.append(data)


def parse_page(*, text: str) -> PageParser:
    """
    Parse a page.

    Parameters
    ----------
    text : str
        The page's HTML.

    Returns
    -------
    PageParser
        What the page holds.
    """
    parser = PageParser()
    parser.feed(text)
    parser.close()
    return parser
