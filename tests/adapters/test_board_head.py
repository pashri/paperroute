"""Tests for the extra stylesheets the board pages link."""

import pytest

from paperroute.adapters.board_head import BoardHead
from paperroute.adapters.web_pin_edit import edit_page

LIVE_LINK = '  <link rel="stylesheet" href="/live.css">\n'


@pytest.mark.parametrize('name', ['../x.css', 'board.css'])
def test_bad_sheet_name_refused(name: str) -> None:
    """A path or a sheet the site already serves is refused."""
    with pytest.raises(ValueError, match='sheet'):
        BoardHead(sheets=(name,))


def test_pin_name_with_sheets_mark_stays_text() -> None:
    """A body reading like the sheets mark stays as written."""
    page = edit_page(
        title='Edit pin',
        notice='',
        body='<p>{{sheets}}</p>',
        head=BoardHead(sheets=('palette.css',)),
    )
    assert '<p>{{sheets}}</p>' in page
    assert LIVE_LINK + '  <link rel="stylesheet" href="/palette.css">\n' in (
        page
    )
    assert page.count('/palette.css') == 1


def test_bad_script_name_refused() -> None:
    """A script name that is not a plain `.js` file is refused."""
    with pytest.raises(ValueError, match='script'):
        BoardHead(scripts=('../x.js',))


def test_pin_name_with_scripts_mark_stays_text() -> None:
    """A body reading like the scripts or nav mark stays as written."""
    page = edit_page(
        title='Edit pin',
        notice='',
        body='<p>{{scripts}} {{nav}}</p>',
        head=BoardHead(scripts=('suggest.js',)),
    )
    assert '<p>{{scripts}} {{nav}}</p>' in page
    assert page.count('<script type="module" src="/suggest.js"></script>') == 1
    assert page.count('<nav ') == 1
