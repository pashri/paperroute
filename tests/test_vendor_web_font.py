"""Tests for the Public Sans vendoring script."""

from pathlib import Path
from types import ModuleType

import pytest

from tests.helpers import load_module

SCRIPT = Path(__file__).parent.parent / 'scripts' / 'vendor_web_font.py'
BLOCK = (
    '/* {name} */\n'
    '@font-face {{\n'
    '  font-family: \'Public Sans\';\n'
    '  src: url(https://fonts.gstatic.com/s/publicsans/v1/{file}.woff2) '
    'format(\'woff2\');\n'
    '}}\n'
)


def load_script() -> ModuleType:
    """
    Import the vendoring script.

    Returns
    -------
    ModuleType
        The script as a module.
    """
    return load_module(name='vendor_web_font', path=SCRIPT)


def test_latin_url_picks_latin_block() -> None:
    """The URL after the latin comment wins over latin-ext."""
    css = (
        BLOCK.format(name='latin-ext', file='EXT')
        + BLOCK.format(name='latin', file='X')
    )
    url = load_script().latin_url(css=css)
    assert url == 'https://fonts.gstatic.com/s/publicsans/v1/X.woff2'


def test_latin_url_without_latin_raises() -> None:
    """CSS with no latin block is an error."""
    css = BLOCK.format(name='vietnamese', file='V')
    with pytest.raises(ValueError, match='no latin block'):
        load_script().latin_url(css=css)
