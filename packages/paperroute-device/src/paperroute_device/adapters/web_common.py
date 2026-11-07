"""Helpers the web pages share: packaged files and form fields."""

import json
from collections.abc import Mapping

from aiohttp import web

from paperroute_device.assets import assets


def asset(*, name: str) -> str:
    """
    Read one of the app's web files.

    Parameters
    ----------
    name : str
        File name within the app's web directory.

    Returns
    -------
    str
        The file contents.
    """
    path = assets().web / name
    return path.read_text(encoding='utf-8')


def asset_bytes(*, name: str) -> bytes:
    """
    Read one of the app's web files as bytes.

    Parameters
    ----------
    name : str
        Path within the app's web directory, such as
        `vendor/tom-select.css`.

    Returns
    -------
    bytes
        The file contents.
    """
    path = assets().web
    for part in name.split('/'):
        path = path / part
    return path.read_bytes()


async def form_fields(*, request: web.Request) -> dict[str, str]:
    """
    Read the string fields of a form or JSON body.

    Parameters
    ----------
    request : web.Request
        The incoming request.

    Returns
    -------
    dict[str, str]
        The string fields found; empty if the body is not readable. A
        repeated form field keeps its last value, so a checkbox after a
        hidden field of the same name wins when ticked.
    """
    if request.content_type == 'application/json':
        try:
            body = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}
    else:
        body = await request.post()
    if not isinstance(body, Mapping):
        return {}
    return {k: v for k, v in body.items() if isinstance(v, str)}
